# SDK content, connector, search, and object exports  `stage-20.1.5`

This stage is shared behind-the-scenes support for people building extensions on top of UFO. It does not do the main work itself. Instead, it provides stable “front doors” into deeper parts of the system, so extension code can import approved names without depending on internal files that may change.

The connector module exposes the public pieces needed to plug in connectors, which are adapters that let UFO talk to outside systems. The sources module does the same for content sources, such as a REST API that can feed data into UFO for syncing. The index module gathers the allowed indexing tools, which prepare content so it can be searched later. The search module exposes the public search interfaces and types, while the memory module exposes types used for memory-style search, where stored information can be found again by meaning or context. The objects module collects names for stored objects, their kinds, ownership rules, and views. The listings module provides helpers for listing results in pages, like showing one screen of items at a time. Together, these files form the SDK’s clean public surface.

## Files in this stage

### Content-source integration
Public SDK facades for connector and source-sync building blocks used by extensions that bring external content into the system.

### `core/src/ufo/sdk/connectors.py`

`util` · `cross-cutting import-time API surface`

This file does not define new behavior itself. Its job is to act like a clean front desk for the connector system. A connector is the bridge between this project and an outside service, such as a tool broker, file source, search provider, or OAuth-based account connection. OAuth means a standard sign-in flow where a user grants access without handing over their password.

The real implementations live deeper in the runtime package, under access-related modules. This file re-exports those names so extension authors can depend on `ufo.sdk.connectors` as the supported interface. That matters because internal runtime modules can change shape over time, while this SDK module can remain the stable contract.

The imported names cover the main parts of the connector flow: providers that start and complete OAuth sign-in, brokers that expose available tools and files, registry and resolver types that help core find the right connector, and helper types for credentials, uploads, catalog pages, broker files, and error guidance. In everyday terms, this file is like a labeled parts counter: it does not build the machine, but it tells outside builders which official parts they are allowed to use and where to pick them up.


### `core/src/ufo/sdk/sources.py`

`other` · `cross-cutting`

This file does not implement syncing itself. Instead, it acts like a neatly labeled toolbox at the front of the project. Extension authors can import the pieces they need from `ufo.sdk.sources` without knowing where those pieces live inside the runtime package.

The tools it exposes describe the main contract for a source extension. A source backend fetches outside records, turns them into `Page` documents, and returns a `SyncResult` that tells core what changed. If a source reads everything every time, it can mark the result as a snapshot, letting core remove old pages that disappeared. If it reads only recent changes, it can report only specific deletes, so core does not accidentally remove pages it never checked.

The file also exposes the REST connector framework. That framework helps extensions read paginated web APIs, shape records into pages, and walk partitioned streams such as repositories, channels, or mailboxes. It includes named exceptions for important outcomes: an expired cursor means core should retry fresh later, a skipped stream means the provider refused access but nothing should be deleted, and a stream fault means the provider returned data the connector cannot safely understand.

Without this file, extension code would need to import many internal runtime paths directly. That would make extensions more fragile whenever the project reorganizes its internals.


### Retrieval and paging interfaces
Stable import points for indexing, listing, memory-search, and search APIs used to expose content for discovery and retrieval.

### `core/src/ufo/sdk/index.py`

`other` · `cross-cutting`

This file is a small but important bridge between extension code and the core indexing system. In plain terms, indexing is how the project turns stored text into searchable pieces, so later the system can find the most relevant chunks of information. Embedding means turning text into numeric meaning-vectors, which helps with similarity search: finding text that is close in meaning, not just text with the same words.

The file does not define new behavior itself. Instead, it re-exports selected names from `ufo.runtime.indexing` as part of the public SDK. That means extension authors can import stable SDK names such as `IndexBackend`, `EmbedClient`, `Chunk`, `Hit`, and `IndexScope` from here, rather than reaching directly into runtime code that may be more private or changeable.

The main idea is a clean contract. An extension can provide an `IndexBackend`, which knows how to store and search chunks of text, and an `EmbedClient`, which knows how to create embeddings in batches. The core system later chooses these implementations by name from configuration, such as `memory.index_backend` and `memory.embed_backend`.

Without this file, extension authors would either need to know the project’s internal module layout or import private runtime pieces directly. This file keeps that boundary simple and intentional, like a service counter that exposes only the tools customers are meant to use.


### `core/src/ufo/sdk/listings.py`

`io_transport` · `cross-cutting`

This file exists so code outside the core runtime can work with portal listings without needing to know where the internal listing code lives. A portal listing is a paged list of results, like showing search results 20 at a time instead of all at once. The file does not define new behavior itself. Instead, it re-exports a small set of names from `ufo.runtime.listings`: `ListingCursor`, `ListingPage`, `MalformedCursor`, `page_of`, and `page_query`.

That matters because extensions need a clean software development kit, or SDK, meaning the public set of tools they are expected to use. If extensions imported directly from the runtime internals, later refactors could break them. By importing from `ufo.sdk.listings`, extension code can depend on this public doorway while the project keeps freedom to reorganize the inside later.

Think of it like a shop counter: the goods come from the stockroom, but customers use the counter, not the stockroom shelves. Here, this file is the counter for keyset paging, a paging style that uses a cursor, or bookmark, to ask for the next slice of results.


### `core/src/ufo/sdk/memory.py`

`data_model` · `import time / extension development`

Provider extensions need to talk about memory search in a shared language: what a memory-search provider is, what a search result looks like, and what the default provider is called. The actual definitions live in `ufo.runtime.memory`, but extension authors should not have to reach directly into the runtime internals. This file solves that by re-exporting the important names through the `ufo.sdk` package, which is the public-facing area meant for outside code.

Nothing new is calculated here. When this module is imported, it simply imports three items from the runtime memory module and makes them available under the SDK path: `DEFAULT_MEMORY_SEARCH_PROVIDER`, `MemoryMatch`, and `MemorySearchProvider`. This is like putting commonly used tools on a labeled shelf near the workshop entrance, even though the tools were made and stored in the back room.

Without this file, extension code would either need to know the runtime module layout or risk depending on internal paths that may change. By keeping this thin public wrapper, the project can reorganize its internals later while preserving a cleaner import path for users of the SDK.


### `core/src/ufo/sdk/search.py`

`data_model` · `cross-cutting`

This file is a small public doorway into the project’s search system. The real search definitions live deeper inside the codebase, in `ufo.runtime.search`, but outside code should not need to know that internal path. Instead, extensions and tools can import from `ufo.sdk.search`, which is meant to be the stable, friendly surface.

The search system has two sides. A search backend implements a `SearchProvider`, meaning it knows how to answer a `SearchQuery` with `SearchResults`. If the backend can also retrieve full web pages, it can support a `FetchRequest` and return a `FetchedPage`. Individual results are represented as `SearchHit` objects.

Think of this file like a reception desk in a large building. Visitors do not need to wander through the building to find the right office; the reception desk points them to the right services using a consistent address. If this file were missing, extension code would have to import from internal runtime modules directly, making it more fragile if the internal layout changes.


### Object-store exports
Public SDK names for object kinds, stores, ownership rules, and related view types.

### `core/src/ufo/sdk/objects.py`

`other` · `cross-cutting import-time public API`

This module is like a front desk for the object system. The real definitions live deeper inside the project, in host, runtime, and schema modules. Instead of asking extension authors to know those internal paths, this file re-exports the names they are allowed to use. That matters because internal code can move around over time, while this SDK module can stay stable.

The objects here describe the kinds of things the system knows about, such as workspaces, conversations, members, credentials, artifacts, surfaces, and agents. It also exposes the main building blocks for listing, linking, owning, reading, and storing those objects. Some exported names represent errors or permission rules, such as admin-only access or an unsupported action.

There is no active logic in this file. Importing it simply makes selected names available under `ufo.sdk.objects`. The comment at the top explains the design rule: SDK packages use named modules like this one, because code is not allowed in `__init__.py` files. Without this file, extension code would have to import from core internals directly, making extensions more fragile and more tightly tied to the project’s private layout.
