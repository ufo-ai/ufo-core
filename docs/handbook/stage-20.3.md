# SDK external capability and data protocol doorways  `stage-20.3`

This stage is shared support for people building extensions around UFO. It is not the main work loop itself. Instead, it provides “doorways”: stable public files that extension code can import from, instead of reaching into UFO’s private internal folders that may change.

Each file opens a doorway to one kind of outside capability or data shape. browser.py defines the public way to talk about browser sessions. connectors.py gathers connector service and OAuth sign-in pieces for linking outside services. index.py exposes tools for search indexes and text embeddings, which turn text into searchable representations. memory.py provides the approved memory-search types. models.py collects model-facing pieces such as messages, model clients, pricing or capability descriptions, and helper functions. objects.py exposes object types and storage interfaces. sandbox.py gathers tools for working with sandboxed execution areas, meaning controlled places where code or data can be carried safely. search.py provides the standard search interface. sources.py collects building blocks for source-sync extensions. Together, these files act like labeled ports on a machine, keeping extension integrations clean and stable.

## Files in this stage

### Connection doorways
Public SDK entry points for extensions that connect to browsers or external connector services.

### `core/src/ufo/sdk/browser.py`

`io_transport` · `cross-cutting`

This file exists to give extension authors a stable, simple place to import the browser connection interface from. The real implementations live in `ufo.browser`, but this SDK file re-exports the important names so outside code does not need to depend on that internal location.

The browser connection here is based on CDP, the Chrome DevTools Protocol, which is a way for software to control or inspect a browser. In plain terms, a provider creates a temporary browser access pass for one turn of work. That pass is represented by a lease. The lease gives the engine the browser URL and any needed connection headers, then is released when the turn ends.

The file also exposes the idea of a durable token, which is like a claim ticket. If a turn is recovered after an interruption, the extension can use that token to reconnect to the same browser session when possible. If the session is gone, the `SessionGone` error communicates that clearly.

There is no new behavior in this file. Its value is in being a clean boundary: extensions import these browser-facing types from the SDK, while the project remains free to organize the underlying engine code elsewhere.


### `core/src/ufo/sdk/connectors.py`

`other` · `cross-cutting`

This file does not implement connector behavior itself. Instead, it acts like a front desk: it points people to the right objects without exposing the whole layout of the building behind it. Extensions use these exported names to describe a brokered connector provider, which is a service that can offer tools, search results, files, uploads, or feed-sync access through UFO.

The main idea is that an extension brings two pieces: an OAuth provider, which knows how a user signs in and grants permission, and a connector broker, which knows what the connected service can do once permission exists. Core UFO code can then drive the user through `/connect`, attach the resulting connector registry to a turn's tool context, and let dynamic connector tools run without knowing each broker's private details.

By re-exporting these classes and helpers from `ufo.connectors` and `ufo.grants`, this file creates a stable SDK surface. If extension code imports from `ufo.sdk.connectors`, the project can reorganize internal files later with less risk of breaking extensions. Without this file, outside developers would need to depend on internal module paths, which makes the extension boundary more fragile.


### Retrieval and model contracts
Stable imports for indexing, memory search, and model-related SDK types and helpers.

### `core/src/ufo/sdk/index.py`

`other` · `cross-cutting`

This file exists to give extension developers one stable place to import the pieces they need for indexing. In this project, an index is the searchable store of text chunks, often searched both by words and by meaning. Extensions can provide their own index backend, which is the part that stores chunks, searches them, and deletes them when needed. They can also provide an embedding backend, which turns text into numeric meaning-vectors so similar ideas can be found even when the words differ.

The file does not define new behavior itself. Instead, it re-exports selected names from `ufo.indexing`. Think of it like a front desk: the real offices are elsewhere, but outsiders are asked to come through this desk so the project can keep a clean and stable public interface.

The docstring explains the intended contract. An extension implements `IndexBackend`, packages it with a backend name, and a deployment chooses it through configuration. Core code then builds that backend at startup using context for the current workspace. The same idea applies to `EmbedClient`, which supplies batched text embeddings. By collecting these imports here, the project can hide the internal layout of `ufo.indexing` while still giving extension authors the important building blocks: chunks, hits, scopes, owner-kind constants, a text chunker, and a helper for chunking, embedding, and inserting text.


### `core/src/ufo/sdk/memory.py`

`data_model` · `cross-cutting`

This file is a small public doorway into the project’s memory-search system. Memory search likely lets a provider look up stored past information and return matching results. Instead of asking extension authors to import directly from the deeper internal module, this file re-exports the key names from `ufo.memory`: the default provider setting, the `MemoryMatch` result type, and the `MemorySearchProvider` interface or base type.

The practical value is stability and clarity. If the internal location of the memory code changes later, the project can keep this SDK file the same, so outside extensions do not break. It is like a building’s front desk: visitors do not need to know which back office holds the records; they just ask at the public counter.

There is no runtime logic here beyond importing and making names available. Its job is to define part of the public software development kit, or SDK, which is the set of tools and types external developers are encouraged to build against.


### `core/src/ufo/sdk/models.py`

`data_model` · `cross-cutting`

This file does not create new model behavior itself. Its job is to act like a clearly labeled front desk for the SDK’s model layer. A developer building an extension can import things such as message shapes, model clients, tool-call blocks, usage records, and OpenAI or Anthropic client helpers from `ufo.sdk.models` without needing to know where those pieces live inside the core package.

That matters because internal files can move or be reorganized over time. If extensions reached directly into those internal locations, small refactors could break outside code. This file creates a stable “seam”: the project promises that these are the model-related building blocks outsiders should use.

Most exported names are data shapes used when talking to language models: messages, text blocks, image blocks, tool calls, tool results, model events, usage counts, and model specifications. It also exposes concrete clients for Anthropic and OpenAI, plus helpers for building OpenAI-compatible messages and SDK clients. In everyday terms, this file is like a parts catalog for anything an extension needs in order to describe a model request, receive a model response, or plug into supported model providers.


### Object and sandbox carriers
SDK doorways for object storage abstractions and sandbox execution tools exposed to extensions.

### `core/src/ufo/sdk/objects.py`

`other` · `cross-cutting import surface for extensions`

This file does not create new behavior. Its job is to provide a stable, friendly import location for code outside the core project, especially extensions. Think of it like a reception desk: the real people work in other rooms, but visitors are told to come to this desk so the building can change internally without confusing them.

The module re-exports object-related names from internal places such as `ufo.objects` and `ufo.conversations`. These names include object identifiers, object rows, pages of object results, owner information, store interfaces, and errors such as an unknown object or an unsupported action. By repeating the names here, the SDK gives extension authors one approved place to import from.

The comment at the top explains an important project rule: `ufo.sdk` keeps its package initializer empty, so public SDK names live in specific modules like this one. Without this file, extension authors might import directly from internal modules. That would make the system harder to change, because any internal rename or reshuffle could break outside code.


### `core/src/ufo/sdk/sandbox.py`

`other` · `cross-cutting import-time public API`

This file does not create new behavior of its own. Instead, it acts like a clearly labeled front desk for the sandbox part of the SDK. A sandbox is the isolated working environment where UFO can run code or connect storage without exposing the rest of the system. Extension authors need a stable place to find the pieces used to describe and work with that environment.

The file re-exports items from deeper internal modules. For example, it exposes the `Carrier` protocol, which is the interface an extension implements to provide a sandbox backend; `CarrierSpec`, which describes how that backend is registered; and value objects such as `SandboxSpec`, `SandboxSession`, `MountSpec`, and `ExecResult`, which describe what to start, how storage is mounted, and what came back from running a command.

It also exposes constants and helper functions for sandbox file-system mounting, such as token paths, timeout values, and commands used to prepare or install access tokens. In plain terms, these are the small shared instructions that let a sandbox safely connect to external storage.

This file matters because it keeps the public SDK surface simple and stable. Without it, users would have to import from internal package paths, which makes their extensions more fragile if the project reorganizes its internals later.


### Search and source interfaces
Public contracts for search integrations and source synchronization extensions.

### `core/src/ufo/sdk/search.py`

`other` · `cross-cutting SDK import surface`

This file does not implement search itself. Instead, it re-exports the search “seam”: the shared contract between the main system and any search backend. A seam is like a plug shape: as long as both sides fit the same shape, the system can swap in different search providers without changing the tools that ask for search results.

The real definitions live in `ufo.search`, but this file exposes them through `ufo.sdk.search`, which is the safer public API for extension authors and callers. That matters because internal module paths can change over time, while SDK paths are meant to be stable.

The exported pieces describe the main search workflow: a caller creates a `SearchQuery`, a `SearchProvider` answers with `SearchResults` made of `SearchHit` entries, and a fetch step can use a `FetchRequest` to retrieve a full `FetchedPage`. If a provider cannot fetch a page, it can raise `SearchUnsupported`, a clear signal that the requested operation is not available for that backend.

Without this file, outside code would need to import directly from `ufo.search`, tying extensions to internal layout and making future refactors more likely to break them.


### `core/src/ufo/sdk/sources.py`

`other` · `cross-cutting import-time API surface`

This file does not implement syncing itself. Instead, it acts like a clearly labeled toolbox shelf for extension authors. If someone wants to make UFO read records from an outside system, such as a REST API, they import the needed pieces from here rather than digging through the internal package layout.

The main idea is a “source backend”: extension code that knows how to fetch records from a provider and turn them into `Page` documents, which UFO can later search or embed. This file exposes the core types involved in that contract, such as `SourceBackend`, `SyncResult`, `Page`, and errors like `CursorExpired` and `StreamSkipped`. Those names describe how a sync run reports what it found, where it should resume next time, and whether a provider refused access in a way that should be treated as skipped rather than broken.

It also exposes a reusable REST connector framework. Instead of every extension writing pagination, record extraction, and partition walking from scratch, authors can use `RestConnector`, `StreamSpec`, `Pagination`, `PartitionWalk`, and helper functions like `records_at` or `get_path`. In plain terms, this keeps each connector focused on the outside service’s shape, while shared machinery handles common chores like walking through pages of API results.

Without this file, extensions would need to import internal modules directly. That would make the project harder to learn and easier to break when internals move.
