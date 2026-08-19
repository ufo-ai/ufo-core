# SDK integration, data access, and surface facades  `stage-19.5`

This stage is shared behind-the-scenes support for people building on top of the system. It does not run the main work itself. Instead, it provides stable public “front doors” into features that live deeper inside the codebase. That matters because extension authors can import from the SDK without depending on private file paths that may change.

Each file is one of these front doors. browser.py exposes the approved way to connect browser extensions to browser transport code. terminal.py does the same for terminal transports, terminal backends, and blob storage. hub.py gathers hub protocol messages, live frames, and activity types used for shared communication. connectors.py exposes connector and OAuth building blocks, while sources.py collects tools for adding content sources, such as REST APIs, pagination, and sync results.

The data-facing doors are index.py for indexing and embeddings, search.py for search interfaces, memory.py for memory search, and models.py for approved model clients and helpers. sandbox.py exposes safe execution tools. surfaces.py gathers the types and helpers for building user-facing surfaces. Together, these files act like a clean reception desk for the larger system.

## Files in this stage

### Transport and hub facades
Stable SDK entry points for browser transport, hub protocol frames, and terminal transport integrations.

### `core/src/ufo/sdk/browser.py`

`other` · `cross-cutting`

This file is a small but important “front desk” for browser integration. The actual browser connection machinery lives in `ufo.browser`, but this file re-exports the pieces that are meant to be part of the public SDK. That keeps extensions pointed at a stable place, even if the internal code moves later.

The concepts it exposes describe how UFO connects to Chrome through CDP, the Chrome DevTools Protocol, which is a way for software to inspect and control a browser. A `CdpProvider` creates a temporary browser connection for a turn of work. That connection is represented by a `CdpLease`, which gives access to a `CdpEndpoint`: the browser URL plus any needed connection headers. When the turn ends, the lease can be released. If work is recovered later, the saved lease token can be used to reattach, unless the browser session has disappeared, in which case `SessionGone` is raised.

It also exposes `FileBytes`, used when a remote browser needs file contents, and `FindCompleter`, a hook that lets host code help rank or complete browser element searches. Without this file, extension authors would have to depend directly on internal browser code, making integrations more fragile.


### `core/src/ufo/sdk/hub.py`

`other` · `cross-cutting import-time public API`

This module does not create new behavior. Instead, it collects important hub-facing names from deeper inside the project and re-exports them under `ufo.sdk.hub`. That matters because SDK users should not need to know the project’s internal folder layout just to work with hubs. It is like a front desk: the real people and tools are elsewhere, but this is the place visitors are told to go.

The hub is the part of the system that connects live activity, tool calls, replies, terminal output, subagent activity, cost updates, and pause/resume-style events. This file exposes the main `Hub` protocol, the in-process implementation `InProcessHub`, and the message or frame types that hub extensions are expected to understand.

The comment at the top explains an important project rule: `ufo.sdk` keeps its `__init__.py` empty, so public SDK symbols live in named modules like this one. Without this file, users would either import from internal modules directly, which makes their code more fragile, or lose a clean public import path for building hub integrations.


### `core/src/ufo/sdk/terminal.py`

`other` · `import time / SDK use`

This module does not define new behavior. Instead, it gathers important terminal-related names from deeper inside the project and re-exports them under `ufo.sdk.terminal`, which is the public-facing SDK path.

That matters because outside code should not need to know the project’s internal folder layout. A terminal transport extension, for example, can import `TerminalTransport`, `TerminalOp`, `TerminalWorkspace`, and `Terminals` from this file without reaching directly into `ufo.sandbox.terminal`. The file also exposes `BlobStore` and `BlobNotFound`, because terminal work may need to read or write larger pieces of data through the system’s shared blob store.

Think of this file like a labeled service counter in a large building. The real offices are elsewhere, but newcomers can go to this counter and get the right forms without learning the whole building map.

The comments explain why this pattern exists: `ufo.sdk` keeps its package `__init__.py` empty, so public SDK names live in small named modules like this one. If this file were missing, external terminal extensions would either break or depend on private internal paths that may change more easily.


### Connector and source facades
Public import paths for extension connectors, OAuth pieces, content sources, REST helpers, pagination, and sync results.

### `core/src/ufo/sdk/connectors.py`

`other` · `cross-cutting import-time SDK surface`

This file does not define new behavior. Instead, it gathers connector and authorization names from deeper modules and re-exports them as part of the public SDK. In plain terms, it is like a reception desk: extensions come here to ask for the standard forms and interfaces they need, while the real offices stay behind the scenes.

The problem it solves is stability. A connector extension needs to describe a brokered provider: how a user authorizes access, what tools or catalog entries the broker offers, how server-side requests are forwarded, and how feed-sync credentials are passed along. Those concrete pieces live in internal modules such as `ufo.connectors` and `ufo.grants`. If extensions imported those internals directly, any internal reorganization could break them. By importing through `ufo.sdk.connectors`, extension authors get a clearer contract: these are the connector-facing building blocks the core project means to expose.

The long module docstring also explains the bigger flow. An extension supplies an OAuth provider, which knows how to start and finish user authorization, plus a connector broker, which knows about available tools, catalog entries, execution, and credentials. Core code can then drive the `/connect` handoff and expose dynamic connector tools without knowing each broker’s private mechanics.


### `core/src/ufo/sdk/sources.py`

`other` · `cross-cutting; used when extensions import the SDK and when source backends are defined`

This file does not implement source syncing itself. Instead, it acts like a clearly labeled toolbox shelf for extension developers. Rather than making an extension import many internal modules from different places, it re-exports the pieces needed to build a source backend.

A source backend is the part of an extension that fetches records from an outside provider and turns them into `Page` documents that the core system can store and later search or embed. The file exposes the main contract, `SourceBackend`, plus supporting types for authentication, pages, sync results, and special outcomes such as an expired cursor or a skipped stream.

It also exposes a REST connector framework. This is for common provider APIs that return records over HTTP. Extension authors can describe streams, pagination, partitioned fetching, and record extraction using shared helper classes and functions instead of rewriting that plumbing every time.

The long module docstring is important because it explains the expected behavior: full snapshots can cause old pages to be tombstoned, incremental syncs only delete explicitly named records, skipped streams are not treated as failures, and malformed provider responses can be reported with a clear backend-authored reason. Without this file, extension authors would need to know the internal package layout and would be more likely to depend on unstable implementation details.


### Data and model facades
Stable SDK doorways for indexing, memory search, model access, and general search interfaces.

### `core/src/ufo/sdk/index.py`

`data_model` · `cross-cutting extension integration`

This file does not define new behavior. Its job is to act like a clean front counter for the indexing system. The real implementations and type definitions live in `ufo.indexing`, but outside extensions should not need to know that internal location. Instead, they import from `ufo.sdk.index`.

The problem it solves is stability. If extension authors imported directly from the internal indexing module, any internal refactor could break them. By re-exporting the important pieces here, the project creates a clear boundary: “these are the indexing tools and contracts extensions may use.”

The exported pieces describe how an extension can plug in a backend for search. An `IndexBackend` is the contract for storing and finding text chunks, including vector search, which means search based on meaning rather than only exact words. `Chunk` represents a piece of text to index, and `Hit` represents a search result. `IndexScope` says what area of the index should be affected when deleting or filtering. `EmbedClient` is the contract for turning text into embeddings, which are numeric representations used for meaning-based search. Constants like `OWNER_KIND_PAGE` and `OWNER_KIND_MEMORY_ITEM` label what kind of thing a chunk came from.

In short, this file is a small but important compatibility layer. It tells extension developers, “use these names from here,” while letting the core project keep its internal organization flexible.


### `core/src/ufo/sdk/memory.py`

`data_model` · `cross-cutting import-time SDK access`

This file does not create new behavior of its own. Instead, it re-exports a few memory-search names from the internal `ufo.memory` module so that outside provider extensions can import them from `ufo.sdk.memory`. In plain terms, it is like a labeled shelf at the front of a store: the actual items are stored elsewhere, but this shelf tells customers where to pick them up safely.

The names it exposes are `DEFAULT_MEMORY_SEARCH_PROVIDER`, `MemoryMatch`, and `MemorySearchProvider`. These are likely used by extensions that want to participate in searching memory: `MemorySearchProvider` describes the provider interface, `MemoryMatch` represents a found result, and `DEFAULT_MEMORY_SEARCH_PROVIDER` points to the standard provider choice.

The main reason this file matters is stability. Internal module paths can change over time, but SDK users need reliable import paths. Without this file, extension authors might import directly from internal modules, making their code more likely to break if the project is reorganized.


### `core/src/ufo/sdk/models.py`

`data_model` · `cross-cutting import time`

This file acts like a front desk for the project’s model API. The real code for talking to model providers, describing messages, representing tool calls, tracking usage, and naming model capabilities lives in deeper internal modules. This file re-exports those pieces under `ufo.sdk.models`, which is the path outside code is meant to use.

That matters because extensions need a safe contract. If an extension imports directly from internal files, small reorganizations inside the project could break it. By importing through this file instead, extensions depend on a public seam: the project can move internal code around while keeping this outward-facing doorway the same.

The exports cover several groups: clients for Anthropic and OpenAI models, shared message and content block types, streaming event types, tool-use structures, pricing and model specification types, usage records, and helpers such as image trimming or OpenAI message conversion. There is no new behavior here. The file simply gathers and renames existing objects so they appear as part of the SDK’s official model surface.

An everyday analogy is a restaurant menu: the kitchen may be complex and change over time, but the menu gives customers one clear, reliable way to ask for what they need.


### `core/src/ufo/sdk/search.py`

`data_model` · `cross-cutting`

This file is like a signposted front desk for search features. The actual search definitions live in `ufo.search`, but outside code is expected to come through `ufo.sdk.search` instead. That matters because extensions need a dependable public path for the pieces they implement or use, even if the project later rearranges its internal files.

The search system has two sides. A search backend implements `SearchProvider`, which answers a `SearchQuery` with `SearchResults`. Each result can contain `SearchHit` items, which are the individual matches. Some providers can also fetch the full page behind a result; for that, they use `FetchRequest` and return a `FetchedPage`.

This file does not add new logic or change the imported objects. It simply re-exports them under the SDK namespace. In plain terms, it says: “If you are building against UFO’s public search interface, import these names from here.” Without this file, extension authors might import from internal locations directly, making their code more fragile when the project changes.


### Execution and surface facades
Public SDK imports for sandbox execution helpers and user-facing surface extension types.

### `core/src/ufo/sdk/sandbox.py`

`other` · `cross-cutting import-time API surface`

This module does not define new behavior. Instead, it gathers important sandbox-related names from deeper inside the project and re-exports them under `ufo.sdk.sandbox`, which is the public-facing path meant for outside code.

The problem it solves is stability. Internally, the project may organize sandbox code across modules such as `ufo.sandbox.session`, `ufo.sandbox.containment`, and `ufo.ext.manifest`. But an extension author should not have to chase those internal locations. They can import things like `SandboxSession`, `Carrier`, `SandboxSpec`, `ExecResult`, and containment helpers from this single module.

In plain terms, this file is like a front desk. The useful tools live in different rooms, but visitors only need to come to one counter to ask for them. That matters because the sandbox is a seam where different execution backends can be plugged in. An extension registers a `CarrierSpec` and implements the `Carrier` protocol, meaning it provides the agreed set of operations needed to run and communicate with a sandbox.

The comments also explain a design rule: `ufo.sdk` keeps its package initializer empty, so public API names live in explicit modules like this one. That makes the public surface clearer and avoids hidden startup code in `__init__.py` files.


### `core/src/ufo/sdk/surfaces.py`

`other` · `cross-cutting; active when extension code imports the public SDK surface API`

A “surface” is an outside place where UFO can meet users or systems, such as a chat interface, inbox, or other integration point. Extension authors need a stable set of building blocks: ways to describe routes, receive context, send writeback results, ask for credentials, work with transcripts, and report errors. This file does not create new behavior itself. Instead, it re-exports those building blocks from their internal homes so extension code does not need to know the project’s private folder layout.

Think of it like a front desk in a large building. The tools are stored in many rooms, but visitors are told to come to one desk to pick up what they need. That keeps the public interface clear even if the internal rooms move around later.

The long list of imports includes surface specifications, route and context types, writeback support, transcript records, credential and connection request types, terminal-related records, workspace file limits, and helper functions for message text and transcript access. The repeated `as SameName` style makes the re-export explicit: these names are intentionally part of this module’s public API.

The comment at the top also explains a project rule: `ufo.sdk` keeps its package initializer empty, so public SDK names live in named modules like this one rather than in `__init__.py`.
