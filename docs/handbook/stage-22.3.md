# SDK Provider, Source, Model, and Sandbox Facades  `stage-22.3`

This stage is shared behind-the-scenes support for extension authors. It does not do the main work itself. Instead, it provides stable “front doors” into parts of the system that may move around internally. That way, outside code can keep importing from the SDK without depending on private file locations.

The browser module exposes safe browser connection interfaces. Connectors gathers the pieces needed to talk to external services, including login support such as OAuth, a standard web sign-in flow. Sources is for adding new content sources, with sync tools, REST helpers, pagination, and errors. Index opens the door to search indexes and embedding backends, which turn text into searchable numeric representations. Memory and search expose the public types used for memory lookup and general search. Models gathers model clients, message formats, tool-call blocks, and pricing helpers. Operator exposes web-session helpers meant only for operator use. Sandbox re-exports the public API for controlled execution environments. Together, these files act like a reception desk for plugins: they route authors to the right tools while shielding them from internal rearrangements.

## Files in this stage

### External Connectivity Facades
Stable SDK import surfaces for browser transport, connector infrastructure, and source synchronization integrations.

### `core/src/ufo/sdk/browser.py`

`io_transport` · `cross-cutting`

This file does not define new behavior itself. Instead, it re-exports a small set of browser-related types from the internal `ufo.browser` module so outside extensions can import them from `ufo.sdk.browser`. That matters because extensions need a clear, supported place to connect their browser provider code to the engine without depending on internal project paths that may change.

The main idea is a “browser transport seam”: a boundary where an extension can supply or reconnect to a Chrome DevTools Protocol session. Chrome DevTools Protocol, often called CDP, is the control channel that lets software drive a Chrome browser. A `CdpProvider` creates a `CdpLease`, which is like borrowing a browser session for one turn of work. The lease gives a `CdpEndpoint`, meaning the URL and headers needed to connect. When the turn ends, the lease can be released. If work is recovered later, the saved lease token can be used to reattach, unless the session is gone.

The file also exposes helper concepts such as `FileBytes`, used when a remote browser needs file contents, and `FindCompleter`, a hook for ranking page elements. In short, this file is an API signpost: it keeps extension-facing imports clean while hiding where the concrete implementation lives.


### `core/src/ufo/sdk/connectors.py`

`io_transport` · `extension loading and connector integration`

Connectors are the way outside services plug into this system. A connector extension can provide login flow support, a catalog of available tools or files, and server-side actions that the core application can call without needing to know the details of that outside service. This file exists to make that boundary clean.

Rather than asking extension authors to import pieces from several internal modules, this file re-exports the important names from `ufo.connectors` and `ufo.grants`. A re-export means: “this thing is defined somewhere else, but you can safely import it from here.” It is similar to a reception desk in a building. Visitors do not need to know which office contains each person; they go to the desk and are directed through a stable, public interface.

The file includes connector concepts such as broker tools, catalog entries, uploaded files, request forwarding, and connector registries. It also includes OAuth-related pieces, where OAuth is the common web login-and-permission flow used by services like Google or Slack. The important point is that this file contains no new behavior. Its job is compatibility and clarity: extensions can depend on `ufo.sdk.connectors` as the public contract, while the core project remains free to organize its internal modules behind the scenes.


### `core/src/ufo/sdk/sources.py`

`other` · `cross-cutting: used when extensions are imported and when source sync code refers to the public SDK surface`

This file does not implement syncing itself. Instead, it acts like a well-labeled toolbox at the edge of the project. An extension that wants to bring outside records into UFO can import the pieces it needs from here, rather than reaching into many internal modules.

The main idea is the “source backend”: a small adapter an extension writes so UFO can ask an outside service for pages of content. The backend returns `Page` objects, plus a `SyncResult` that tells core whether this run was a full snapshot or only a partial update. That distinction matters. In a full snapshot, missing pages can be treated as deleted. In an incremental update, only explicitly named deletions are removed, so older pages are not accidentally wiped out.

The file also exposes helper classes for REST-based sources. A REST source talks to a web API, usually page by page, and may need cursors, partitions, or pagination rules. The exported connector tools provide common ways to walk through those records without every extension reinventing the same machinery.

Important exceptions are also re-exported. `CursorExpired` tells core to discard an unusable saved cursor and retry fresh. `StreamSkipped` means a provider refused a stream for this account, so the run should be recorded as skipped rather than failed. Without this file, extension code would be tied to internal module paths and the public SDK would be harder to use safely.


### Retrieval and Knowledge Facades
Public re-export modules for indexing, memory search, and search interfaces used by extensions and backend plugins.

### `core/src/ufo/sdk/index.py`

`other` · `cross-cutting`

This file does not implement indexing itself. Instead, it acts like a clearly marked service counter for extension authors. The real search and embedding shapes live deeper in `ufo.indexing`, but extensions should not have to depend on that internal location directly.

The file exposes the main pieces an extension needs to join the indexing system. An `IndexBackend` is the plug-in point for a search backend that can store chunks of text, search them using keywords and vectors, and delete entries within an `IndexScope`. A `Chunk` is a piece of text prepared for indexing, and a `Hit` is a search result. The owner-kind constants distinguish what kind of thing a chunk came from, such as a page or a memory item. `EmbedClient` is the plug-in point for turning batches of text into embeddings, which are numeric representations used for similarity search. `TextChunker` and `chunk_embed_upsert` support the common flow of splitting text, embedding it, and storing it.

Why this matters: it gives extensions a stable import path, `ufo.sdk.index`, even if the internal indexing package changes later. Without this file, extension code would have to reach into internal modules, making it more fragile and harder to keep compatible.


### `core/src/ufo/sdk/memory.py`

`data_model` · `cross-cutting`

This file is a small public-facing bridge. The project has memory-search concepts defined elsewhere, in `ufo.memory`, but people writing provider extensions should not need to know the internal layout of the codebase. Instead, they can import the supported memory-search pieces from `ufo.sdk.memory`.

It re-exports three names: `DEFAULT_MEMORY_SEARCH_PROVIDER`, `MemoryMatch`, and `MemorySearchProvider`. In plain terms, these give extension code a shared way to talk about searching stored memory, describing a found result, and referring to the default search provider. Re-exporting means this file does not create new behavior. It simply makes existing definitions available under a cleaner, more stable SDK path.

Why does this matter? If outside extensions imported directly from internal modules, future code rearrangements could break them. This file helps protect those users by acting like a public signpost. Internals can move later, while the SDK import path can stay the same.


### `core/src/ufo/sdk/search.py`

`data_model` · `cross-cutting`

This file exists to make the project’s search system easier and safer to plug into. A search extension needs to know the shapes of the requests and responses it must work with: a search question, a list of results, a request to fetch a page, and the fetched page itself. It also needs the provider interface, which is the contract saying, in effect, “if you want to be a search backend, these are the things you must be able to do.”

Rather than defining those pieces here, this file re-exports them from `ufo.search`. Re-exporting means it imports names from one place and makes them available from another. The everyday analogy is a reception desk: the real offices are elsewhere, but outsiders are told to come through this desk because it is the official, stable entrance.

That matters because SDK users should not have to depend on the project’s internal layout. If internal files move later, this file can keep the public import path the same. Without it, extensions might import private modules directly, making them easier to break during refactors. The file also makes the intended boundary clear: extensions implement `SearchProvider`, answer `SearchQuery` with `SearchResults`, fetch pages through `FetchRequest`, return `FetchedPage`, and can raise `SearchUnsupported` when a fetch cannot be done.


### Model and Execution Environment Facades
SDK doorways for model clients and messages, operator web-session helpers, and sandbox execution APIs.

### `core/src/ufo/sdk/models.py`

`other` · `cross-cutting`

This file solves a simple but important problem: it gives outside code one reliable shelf to pick up all model-facing building blocks. Without it, extensions would need to import directly from deeper internal files such as OpenAI, Anthropic, interface, pricing, and schema modules. That would make extensions fragile, because an internal file move could break them even if the public behavior stayed the same.

The file does not create new behavior. Instead, it re-exports selected names. A re-export means “bring this thing in from its real home, then make it available here too.” It is like a shop window: the goods are made elsewhere, but this is the place customers are meant to browse.

The exported pieces include client classes for Anthropic and OpenAI models, shared request and response shapes, message and content block types, tool-call types, reasoning/thinking blocks, usage records, pricing information, model specifications, and helpers such as image trimming and OpenAI message conversion. Together, these form the “model seam”: the boundary where extensions can talk to language models without reaching into the project’s private machinery.

The important behavior is social rather than computational: this file marks what the project promises as a compatible public API for model integrations.


### `core/src/ufo/sdk/operator.py`

`util` · `cross-cutting`

This file is a small bridge between the public SDK area and the internal operator web tools. The operator surface is the part of the system meant only for operators or debug tools, not ordinary users. Those tools need two shared pieces of sign-in behavior: one piece figures out which workspace an operator is trying to access, and another binds the operator session to the shared session cookie after a POST request.

Rather than making every caller know the internal path `ufo.ext.operator`, this file exposes the two helpers from `ufo.sdk.operator`. That matters because public import paths are like street addresses: once other code starts using them, moving the real implementation should not break everyone. This file keeps that address stable while the actual logic lives elsewhere.

There are no functions or classes defined here. It simply imports `bind_operator_session` and `resolve_operator_workspace` and makes them available under the SDK namespace. If this file disappeared, operator debug tools that import from the SDK path could fail even though the underlying logic still exists.


### `core/src/ufo/sdk/sandbox.py`

`other` · `cross-cutting import-time public API`

This module is like a clearly labeled service counter for sandbox features. The real sandbox code lives elsewhere, mostly in `ufo.sandbox.session`, but outside extensions should not have to know that internal layout. Instead, they can import from `ufo.sdk.sandbox` and get the pieces they need: sandbox descriptions, session handles, execution results, proxy settings, and the `Carrier` protocol, which is the contract a backend must follow to provide sandbox support.

The file also re-exports `CarrierSpec` from the extension manifest area. A `CarrierSpec` describes how an extension registers a sandbox carrier, while the `Carrier` protocol describes what that carrier must be able to do. In plain terms, this is the seam where one deployment can swap in a different sandbox backend without changing the code that uses sandboxes.

The comment explains an important project rule: `ufo.sdk` keeps its package initializer empty, so named modules like this one define the public surface. Without this file, extension authors would need to import from deeper internal modules, making their code more fragile if the project reorganizes its internals.
