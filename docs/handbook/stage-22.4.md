# Public SDK model, retrieval, memory, and accounting facades  `stage-22.4`

This stage is shared support for extension authors. It does not run the main work of searching, remembering, calling models, or calculating costs. Instead, it creates stable “front doors” in the public SDK, so outside code can use the project’s approved interfaces without depending on private internal file paths that may change.

The models facade gathers the main model-facing pieces in one place: message shapes, model client interfaces, pricing and specification types, and helper functions. The index facade exposes the contracts needed to plug in a search index or embedding service, where embeddings are numeric representations used for finding similar content. The search facade publishes the search-related types that tools and extensions use to ask for and return search results. The memory facade does the same for memory-search provider types, which are used to look up stored past information. The accounting facade publishes the project’s spending and accounting vocabulary, so extensions can describe cost-related data consistently. Together, these files act like a clean reception desk for the SDK.

## Files in this stage

### Accounting vocabulary
Stable SDK exports for accounting and spending types used by extensions.

### `core/src/ufo/sdk/accounting.py`

`data_model` · `cross-cutting`

This file is a small public doorway into the project’s accounting data. The real accounting definitions live in `ufo.accounting`, but users of the software are meant to import public SDK pieces from named modules under `ufo.sdk`. This file makes that possible for spending reports.

In plain terms, it is like a shop counter that displays selected items from the stockroom. The stockroom is `ufo.accounting`; this SDK module is the counter where outside code can safely ask for those items by name.

The exported names describe spend and usage information: reports for agents and members, totals grouped by subject or dimension, usage exports, a conversion constant for micro-dollars, and a helper for finding metered workspaces. A surface can use these value objects to show a workspace spending rollup, and the command-line tool `ufoctl spend` can print the same kind of sums.

Nothing in this file transforms data, reads files, talks to a network, or performs calculations. Its importance is stability and clarity: if external users import from `ufo.sdk.accounting`, the project can keep a clean public API even if the internal accounting code is reorganized later.


### Retrieval interfaces
Public SDK doorways for indexing, memory search, and general search contracts.

### `core/src/ufo/sdk/index.py`

`data_model` · `cross-cutting`

This file does not create new behavior of its own. Its job is to make a clean, stable contract between the core application and outside extensions. In plain terms, it is like a labeled service counter: extension authors come here to get the approved shapes and names they need, rather than wandering through the back rooms of the codebase.

The exported pieces describe how an extension can provide an index backend: a component that stores and searches chunks of text, using both normal word matching and vector search, which means search based on mathematical representations of meaning. It also exposes the embedding client interface, which is the part that turns text into those vectors. The file also makes available supporting concepts such as chunks of text, search hits, index scopes for deletion, owner-kind constants that say what kind of object a chunk belongs to, and a helper for chunking, embedding, and inserting text.

This matters because extensions need a dependable import path. If they imported directly from the internal `ufo.indexing` module, future reshuffling inside the project could break them. By re-exporting only the intended public pieces here, the project keeps a clearer boundary between the stable extension API and the internal implementation.


### `core/src/ufo/sdk/memory.py`

`data_model` · `cross-cutting`

This file exists so extension authors can import memory-search building blocks from a stable SDK path instead of reaching into the project’s internal modules directly. In plain terms, it is a signpost: if an outside provider wants to describe a memory search result or plug in a memory search service, this is the place the project wants them to import from.

The file does not define new behavior of its own. Instead, it re-exports three names from `ufo.memory`: `MemoryMatch`, which represents a found memory result; `MemorySearchProvider`, which describes the provider interface that a memory-search plugin should follow; and `DEFAULT_MEMORY_SEARCH_PROVIDER`, which names or points to the default provider choice.

This matters because public SDK files create a safer boundary. Internal code can move around later, but extensions can keep using `ufo.sdk.memory` as the public contract. Without this file, provider authors might depend on internal paths, making their extensions more likely to break when the project is reorganized.


### `core/src/ufo/sdk/search.py`

`data_model` · `cross-cutting`

This file does not implement search itself. Instead, it acts like a signposted front desk: anyone building a search extension or calling a search tool can come here to get the official names for the search pieces they need. Those pieces include a search request (`SearchQuery`), search results (`SearchResults` and `SearchHit`), optional page fetching (`FetchRequest` and `FetchedPage`), and the provider interface (`SearchProvider`) that a backend must implement.

The reason this matters is stability. The real definitions live in `ufo.search`, but external code should not need to know that internal location. By re-exporting the names here, the project can present a clean SDK surface: extension authors import from `ufo.sdk.search`, and the project keeps freedom to reorganize internals later.

In practical terms, an extension declares that it can provide search, implements the `SearchProvider` contract, and exchanges these request and response objects with the rest of the system. Without this file, extension authors would have to depend on internal module paths, which makes their code more fragile and harder to understand.


### Model interfaces
Stable SDK exports for model clients, messages, pricing, specs, and related helpers.

### `core/src/ufo/sdk/models.py`

`data_model` · `cross-cutting`

This file does not define new behavior. Its job is to gather the model-facing parts of the project and re-export them as the supported SDK surface. In plain terms, it is like a front desk: extension authors can ask here for the official objects they need, rather than wandering through the building and relying on private room names that may change.

The exported items cover the main pieces used when talking to language or vision models: message and content block types, streaming event types, tool-call shapes, model request and response markers, client classes for Anthropic and OpenAI, model specification and pricing records, and helper functions such as trimming images or building OpenAI-compatible messages.

This matters because it creates a boundary between public extension code and the project’s internal layout. Without this file, outside code might import directly from internal modules such as `ufo.models.interface` or `ufo.models.openai`. That would make extensions more fragile: a harmless internal refactor could break them. By re-exporting these names here, the project can keep a clearer promise about what extension code is allowed to use.
