# Public SDK Provider, Search, Job, and Capability Facades  `stage-23.4`

This stage is shared support for extension authors. It does not run the browser, search engine, jobs, or terminal itself. Instead, it provides stable “front doors” in the public SDK, so outside code can import approved names without depending on deep internal file paths that may change.

Each file is one doorway for a different kind of extension work. The browser and terminal SDK files expose the allowed connection types for talking to browsers and terminals. The connectors and sources files expose tools for bringing outside content into UFO, including source helpers and source error types. The search, index, and memory files expose the interfaces used to plug in search systems, embedding or indexing backends, and memory search providers. The skills file exposes the public objects needed to define extension skills. The jobs file exposes safe types for describing background work. The hub file exposes the hub interface and live event objects, so extensions can react to system activity. Together, these files act like a reception desk: extensions ask here for the official tools, while the engine stays free to reorganize internally.

## Files in this stage

### Connection and event facades
Stable SDK doorways for extension-facing browser connections, connector APIs, and hub event interfaces.

### `core/src/ufo/sdk/browser.py`

`io_transport` · `cross-cutting browser connection setup and per-turn leasing`

This file does not create new behavior. Instead, it re-exports a small set of browser-related types from `ufo.browser` under the public `ufo.sdk.browser` path. That matters because extensions need a safe, stable way to talk about browser connections without depending on the project’s private internal layout.

The main idea is a “browser transport seam”: a boundary where an extension or deployment can provide a Chrome DevTools Protocol connection. Chrome DevTools Protocol, or CDP, is the control channel used to drive a browser from code. A `CdpProvider` can create a temporary `CdpLease` for one turn of work. That lease gives access to a `CdpEndpoint`, which contains the browser connection URL and any needed headers. When the turn ends, the lease can be released.

The file also exposes support pieces. `SessionGone` signals that a saved browser session can no longer be reattached. `FileBytes` is a delayed way to read file contents, useful when a remote browser needs a local file copied to somewhere it can open. `FindCompleter` is a callback hook used when the browser engine asks host-side code to help rank or complete element searches.

Like a reception desk, this file points outsiders to the right official names while hiding which back office they come from.


### `core/src/ufo/sdk/connectors.py`

`other` · `cross-cutting import-time API surface`

This file does not define new behavior of its own. Its job is to act like a front desk for the connector system: it gathers the important connector and OAuth pieces from internal modules and re-exports them under `ufo.sdk.connectors`, which is the safer public path for outside code to use.

Connectors are the project’s way for extensions to plug in outside services, such as a brokered data provider. An extension can describe how users authorize access through OAuth, which is a standard sign-in-and-permission flow, and how the system should talk to the broker that provides tools, files, search, catalog entries, or feed-sync credentials. Core code can then drive the connection flow without knowing each broker’s private details.

The important design choice here is separation. The concrete definitions live in internal modules like `ufo.access.connectors` and `ufo.access.grants`, but extension authors are expected to reach them through this file. That gives the project room to reorganize internals later while keeping the public SDK import path steady. Without this file, extensions would have to import internal implementation details directly, making them more fragile and harder to support.


### `core/src/ufo/sdk/hub.py`

`other` · `import time / SDK use`

This file does not create new behavior. Its job is to make the project’s hub API easy and safe to find. A hub is the part of the system that receives and reports live activity, such as arrivals, replies, pauses, resumes, costs, and final terminal events. Extensions that want to plug into UFO can import the `Hub` protocol, the in-process hub implementation, and the different live-frame event types from this single public module.

The file exists because the package avoids putting code in `__init__.py` files. Instead of making users import from internal paths like `ufo.hub`, the SDK exposes named modules such as this one. Think of it like a reception desk: the actual offices are elsewhere, but visitors get a clear public counter where they can ask for the right forms.

Each imported name is re-exported under the same name. That means `ufo.sdk.hub.Hub` is the same object as `ufo.hub.Hub`; this file simply makes it part of the supported public surface. Without this file, extension authors would either need to know internal module paths or the SDK would have no clean hub import location.


### Search and job contracts
Public re-export points for indexing backends, background job descriptions, memory search, and general search APIs.

### `core/src/ufo/sdk/index.py`

`other` · `cross-cutting`

This file acts like a clearly labeled service counter for the project’s indexing system. Instead of asking extension authors to know where the real indexing code lives inside `ufo.indexing`, it gathers the approved public pieces in one place under `ufo.sdk.index`.

The main idea is that an extension can provide an `IndexBackend`, which is the part that stores and searches text chunks. A “chunk” is a smaller piece of text prepared for search. Searches return `Hit` objects, which represent matching results. The backend can also delete indexed data by an `IndexScope`, which describes the area of stored index data to affect.

The file also exposes `EmbedClient`, the interface for turning batches of text into embeddings. An embedding is a list of numbers that captures the meaning of text so it can be compared by similarity. The project can choose which embedding backend to use through configuration.

Nothing new is implemented here. Every name is imported from `ufo.indexing` and re-exported. That matters because it creates a stable, intentional boundary: extensions depend on this SDK-facing file, while the core project is free to organize its internal indexing code behind it.


### `core/src/ufo/sdk/jobs.py`

`util` · `cross-cutting`

This file is like a labeled service counter for background job tools. The real code lives deeper in the system, but extensions are told to come here instead of reaching into those private shelves directly. That matters because internal modules can change, while this public module is meant to stay stable.

The main ideas it exposes are tools for deciding which workspaces a job should run for, and a few shared names that keep extension code in sync with the core dispatcher. A workspace is a separate area of data or activity. A background job may not need to run everywhere, so an extension can provide an `owner_candidates` builder: a small query recipe that, each time the scheduler ticks, finds the workspace IDs where there is work to do. Rebuilding that query each tick lets the job use the current time, for example to find records that have just become due.

The file also exposes `PAGE_CHANGE_CURSOR_KEY`, a shared prefix used when remembering how far a page-change consumer has read. By importing this constant, an extension can reset that saved position without guessing the core system’s key format. In short, this file keeps extension job declarations clear, stable, and separated from internal implementation details.


### `core/src/ufo/sdk/memory.py`

`data_model` · `cross-cutting`

This is a small public-facing bridge file. Its job is to make selected memory-search pieces available through the SDK, which is the part of the project intended for outside extensions or provider plugins. In plain terms, it is like a front desk: the real items live elsewhere, but this file tells outside code, “come here to get them.”

It re-exports three names from the internal `ufo.memory` module. `DEFAULT_MEMORY_SEARCH_PROVIDER` is the default provider used for memory search. `MemoryMatch` represents a found memory result. `MemorySearchProvider` is the interface or shape that a provider should follow if it wants to supply memory-search behavior.

Without this file, extension authors would likely have to import directly from `ufo.memory`. That would make outside code more tightly tied to the project’s internal layout, so future reorganizations could break plugins unnecessarily. By keeping these names available under `ufo.sdk.memory`, the project creates a cleaner boundary between internal implementation and public extension API.


### `core/src/ufo/sdk/search.py`

`data_model` · `cross-cutting import-time API surface`

This file is a thin public doorway for the search system. The real search definitions live deeper inside the project, in `ufo.search`, but outside code should not have to depend on that internal location. Instead, this file re-exports the important search pieces under the SDK, which is the part of the project meant for extensions and tools to use.

The search system has two sides. An extension can provide a search backend by implementing `SearchProvider`, which answers a `SearchQuery` with `SearchResults`. A result contains `SearchHit` items, meaning individual matches. Some providers can also fetch a page after search; for those, `FetchRequest` describes what to retrieve, and `FetchedPage` is the returned page content.

An everyday analogy is a reception desk. Visitors ask at the SDK desk for “the search forms,” and the desk hands them the right forms from the back office. The visitor does not need to know which cabinet the forms came from. That matters because the project can reorganize its internal code later while keeping the public SDK import path steady.


### Skill and source extensions
SDK facades for extension-provided skills and content sources that plug external content or behavior into UFO.

### `core/src/ufo/sdk/skills.py`

`util` · `cross-cutting`

This module is like a public signpost at the edge of the project. The real skill code lives deeper inside `ufo.skills.runtime`, but outside code should not have to know that internal layout. Instead, an extension that wants to describe or parse skills can import names from `ufo.sdk.skills`.

It re-exports four public items: `RuntimeSkill`, `SkillCard`, `parse_skill_content`, and `skill_root`. In plain terms, these are the building blocks for describing skills, reading skill declarations, and finding where skill content belongs. By putting them here, the project creates a cleaner and more stable software development kit, or SDK, which is the set of tools meant for other developers to use.

This matters because internal folders can change over time. If extensions imported directly from `ufo.skills.runtime`, those extensions would be more likely to break when the project is reorganized. This file acts as a small adapter: it keeps the public import path steady while the implementation can remain elsewhere. The comment also explains why this exists as a named module instead of being placed in `__init__.py`: this project forbids executable code in package initializer files, so public SDK exports live in explicit files like this one.


### `core/src/ufo/sdk/sources.py`

`other` · `extension import and source sync setup`

This file does not implement syncing itself. Instead, it acts like a well-labeled toolbox laid out for extension developers. If someone wants to make UFO read content from a provider, such as a mail system, chat service, repository, or REST API, this is the module they can import from instead of digging through UFO’s internal source packages.

The main idea is a “source backend”: a small plugin that knows how to fetch records from an outside system and turn them into UFO `Page` documents. The file exposes the core shapes involved in that contract, including `SourceBackend`, `SyncResult`, `Page`, source authentication, and the special exceptions that tell core whether a run should be retried, skipped, or treated as a provider data problem.

It also exposes a ready-made REST connector framework. A REST connector is for services reached over HTTP, where data usually arrives in pages and may need pagination, cursors, partition-by-partition fetching, and record extraction from nested JSON. By re-exporting helpers like `RestConnector`, `StreamSpec`, `Pagination`, `PartitionWalk`, and record-shaping utilities, this file gives connector writers one consistent public API.

Without this file, extensions would have to import from internal implementation modules directly. That would make extension code more fragile, because internal module names and layout can change more easily than this SDK-facing surface.


### Terminal integration facade
The stable public import surface for terminal-related SDK types exposed to outside code.

### `core/src/ufo/sdk/terminal.py`

`io_transport` · `cross-cutting`

This module is like a front desk for terminal support in the SDK. The real terminal code lives elsewhere, mainly under `ufo.sandbox.terminal`, and blob storage lives under `ufo.blob`. Rather than asking extension authors to know those internal paths, this file gathers the important public names into `ufo.sdk.terminal`.

That matters because terminal-transport extensions need a small shared vocabulary: what a terminal operation is, what errors can happen, how terminal workspaces are represented, and how to reach stored binary data through the blob store. By re-exporting these names, the project can offer a cleaner public interface while keeping its internal folder layout flexible.

There is no runtime logic here. Importing this file simply makes names such as `TerminalTransport`, `Terminals`, `TerminalOp`, `TerminalAbsent`, `TerminalGone`, `TerminalOpFailed`, `BlobStore`, and `BlobNotFound` available from one SDK-facing module. It also exposes timing constants used by terminal operations. If this file were missing, extension authors would have to import from internal modules directly, which would make their code more fragile and more tightly tied to the project’s private structure.
