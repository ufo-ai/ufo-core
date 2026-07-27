# Public SDK backend, connector, model, and search integration facades  `stage-18.2`

This stage is the public front door for extension authors. It is not the main work loop itself. Instead, it is shared support that keeps outside code safely connected to the engine without depending on the engine’s private folder layout. Each file acts like a labeled counter in a service desk, forwarding users to the right internal tools.

The browser file exposes the allowed browser connection types. The connectors and authproxy files provide stable imports for feed-sync connectors, OAuth, and credential handling. The sources file gathers the pieces needed to build a content source, including sync, pagination, REST connector helpers, and subject types. The hub file exposes hub and model-interface types used to coordinate work. The models file gathers model clients, messages, tools, pricing records, and API descriptions. The sandbox file exposes sandbox backend types and helpers. The index file provides search and embedding backend interfaces, while memory and search re-export the project’s memory-search and search interfaces. Together, these files form a stable SDK layer that lets extensions plug in without reaching into internal code.

## Files in this stage

### Connector and source facades
Stable SDK entry points for authentication, browser access, connector types, and source-backend extension work.

### `core/src/ufo/sdk/authproxy.py`

`other` · `extension setup and credential resolution`

This file is a small public bridge. Its job is to make the authentication-proxy pieces available from the SDK, which is the part of the project extension authors are meant to use. An authentication proxy is a plugin-style component that turns a source’s account reference into the actual credential a connector needs when talking to an outside provider.

The file does not create new behavior itself. Instead, it re-exports four names from the internal connector and manifest modules: `AuthProxy`, `Credential`, `DIRECT_ACCOUNT`, and `AuthProxySpec`. Re-exporting means it imports something from one place and makes it available from another, like putting commonly used tools on the front counter instead of asking visitors to search the stockroom.

This matters because extensions need a clear contract. An extension declares an `AuthProxySpec`, implements an `AuthProxy`, and may be selected through configuration when more than one credential backend exists. Sources marked with `DIRECT_ACCOUNT` use the selected authentication backend to resolve their credentials. Sources that use a broker grant get their credential through that broker instead.

Without this file, extension authors would have to depend on deeper internal module paths such as `ufo.connectors`, making the extension API more fragile if the project later reorganizes its internals.


### `core/src/ufo/sdk/browser.py`

`io_transport` · `cross-cutting extension integration`

This file is a small but important boundary marker. The project can talk to browsers through CDP, the Chrome DevTools Protocol, which is a standard way for tools to control a browser tab: open pages, inspect elements, click, type, and so on. Extensions may need to provide or reconnect to that browser access, but they should not depend directly on the internal `ufo.browser` module.

So this file re-exports a carefully chosen set of names from `ufo.browser`. Think of it like a service desk window: the real work happens behind the wall, but outside users are told to come to this window because its shape is meant to stay stable.

The exported pieces describe the browser connection flow. A `CdpProvider` can create a temporary `CdpLease` for one turn of work. That lease gives a `CdpEndpoint`, meaning the browser address and any needed connection headers. The lease also has a token that can be saved and used later to reattach if work resumes after interruption. If the old browser session no longer exists, `SessionGone` signals that clearly. `FindCompleter` is a hook the browser engine can call when it needs host-side help ranking or completing element matches.

Without this file, extension code would have to reach into internal browser implementation paths, making upgrades more fragile.


### `core/src/ufo/sdk/connectors.py`

`other` · `cross-cutting import/API boundary`

This file does not define new behavior. Instead, it gathers and re-publishes the important connector building blocks from deeper parts of the system. Think of it like a reception desk: outsiders do not need to know which office each form lives in; they can come here and get the official versions.

Connectors are how outside services plug into the system. An extension can provide an OAuth provider, which is the part that helps a user authorize access, and a connector broker, which is the part that describes available tools, runs server-side actions, and supplies credentials for feed syncing. Core code can then drive the connection flow, attach the connector registry to a tool context for a user turn, and route credentials without knowing the private details of each broker.

The practical value of this file is stability. If the internal modules `ufo.connectors` or `ufo.grants` change shape later, the SDK can keep this file as the public contract. Without this re-export layer, extension authors would have to import from internal locations directly, making their code more fragile and more tightly tied to implementation details.


### `core/src/ufo/sdk/sources.py`

`io_transport` · `extension development and source sync setup`

This file does not define new behavior. Instead, it acts like a front desk for the source system: extension code can import the pieces it needs from `ufo.sdk.sources` without knowing the project’s deeper internal folder layout.

A “source” is an integration that brings outside content into the system, such as records from a provider’s API. Extension authors implement a `SourceBackend`, whose job is to fetch outside records and turn them into `Page` documents. The sync result tells core whether the run was a full snapshot, where missing old pages should be removed, or an incremental update, where only explicitly named deletions are removed.

The file also exposes the REST connector framework. That framework helps integrations read paginated web APIs, shape returned records, and walk partitioned streams such as one cursor per repository or channel. It includes helper types for pagination, stream pages, partition walking, and common JSON helpers like `get_path` and `records_at`.

Two important exceptions are also re-exported. `CursorExpired` means a saved resume point no longer works, so core should refetch from scratch. `StreamSkipped` means the provider refused a stream for a non-fatal reason, such as missing permission, so the sync should be recorded as skipped rather than failed.

Without this file, extension authors would have to import from many internal modules directly, making extensions more fragile when the internal code is reorganized.


### Hub and model facades
Public import surfaces for hub orchestration types, model clients, tool definitions, pricing records, and sandbox integration helpers.

### `core/src/ufo/sdk/hub.py`

`orchestration` · `import time`

This module is like a clearly labeled front desk for hub features. The real implementations live elsewhere, mostly in `ufo.hub`, but users of the SDK should not have to know every internal file path. Instead, they can import common hub concepts from `ufo.sdk.hub`.

A hub here is the part of the system that coordinates live activity, such as frames of work, tool calls, terminal events, cost updates, and skill loading. This file exposes the public names for those ideas: `Hub`, `InProcessHub`, `LiveFrame`, `ToolCall`, `Terminal`, `CostTick`, `SkillLoad`, `Parked`, and `TextDelta`.

The file exists partly because this project keeps package `__init__.py` files empty. That means public SDK entry points are placed in named modules like this one rather than being gathered in package initializer files. Without this module, SDK users would need to import these names from deeper internal locations, which would make their code more tightly tied to the project’s internal layout and harder to keep stable over time.

There is no runtime logic here beyond importing and re-exporting names. Its importance is in shaping the public interface: it says, “these are the hub-related pieces outsiders are meant to use.”


### `core/src/ufo/sdk/models.py`

`other` · `cross-cutting; active when SDK users import model-related public API names`

This file solves a simple but important problem: it gives outside code a safe, official place to import the model layer from. The project has model clients for services like Anthropic and OpenAI, shared message shapes, tool-call records, image and text blocks, usage records, and model specification types. Those pieces live in several internal modules, but extensions should not need to know that layout. If they imported from the internal locations directly, a future refactor could break them even if the public behavior stayed the same.

Think of this file like a reception desk in a large building. Visitors do not need to know which office each person sits in; they ask at the desk and get routed to the right place. Here, the “visitors” are SDK users, and the “desk” re-exports selected names such as `ModelClient`, `ModelRequest`, `ToolSchema`, `OpenAIClient`, `AnthropicClient`, `ModelSpec`, and `Usage`.

There is no new behavior here. The file does not create objects, call APIs, or transform data. Its job is to define the supported seam between the SDK and the model internals: these are the model-related building blocks outside code is meant to rely on.


### `core/src/ufo/sdk/sandbox.py`

`other` · `cross-cutting import-time public API`

This module does not define new behavior of its own. Instead, it re-exports selected sandbox tools from deeper inside the project under the public `ufo.sdk.sandbox` name. In plain terms, it is like a clearly labeled front desk: outsiders come here to ask for sandbox building blocks, while the actual offices remain elsewhere.

The sandbox is the part of the system that represents and runs an isolated working environment. Extensions can register a `CarrierSpec`, which describes a backend they provide, and implement the `Carrier` protocol, which is the agreed shape of code that can create and operate a sandbox session. This file exposes those public pieces, along with value objects such as `SandboxSpec`, `MountSpec`, `ExecResult`, and `ProxyEndpoint`.

It also exposes helper constants and functions for filesystem mounting, such as token paths, timeout values, and commands used to prepare or check mounts. These are re-exported so plugin or extension code can use them without importing private internal modules directly.

Without this file, users of the SDK would have to depend on internal paths like `ufo.sandbox.session` or `ufo.sandbox.fs_mount`. That would make their code more fragile, because internal files can move or change. This module gives the project a stable public surface while keeping package `__init__.py` files empty by design.


### Retrieval and search facades
Stable SDK doorways for extension authors integrating index backends, memory search, and general search interfaces.

### `core/src/ufo/sdk/index.py`

`other` · `extension development and startup wiring`

This file does not define new behavior. Its job is to re-export the official index and embedding building blocks from `ufo.indexing` under the public `ufo.sdk.index` path. Think of it like a front desk: the real offices are elsewhere, but outsiders are told to come here so the project can keep its internal layout flexible.

Extensions use these names to contribute a backend for memory search. An `IndexBackend` is the plug-in shape for storing and searching chunks of text, including both keyword-style search and vector search, where vectors are numeric representations of meaning. `EmbedClient` is the matching plug-in shape for turning batches of text into those numeric representations. `Chunk`, `Hit`, and `IndexScope` describe the pieces being indexed, the results returned, and the part of the index being changed or deleted. The owner-kind constants mark what kind of thing a chunk belongs to, such as a memory item or a page.

This matters because extensions should depend on a stable SDK contract, not on private core modules. If this file were missing, extension authors would have to import from `ufo.indexing` directly, which would make the system harder to evolve without breaking them.


### `core/src/ufo/sdk/memory.py`

`data_model` · `cross-cutting`

This file is a small doorway between provider extensions and the project’s memory-search system. Instead of asking outside code to import directly from `ufo.memory`, it exposes the important names through `ufo.sdk.memory`, which is meant to be the public, supported path.

The exported pieces describe how memory search works at the boundary of the system. `MemorySearchProvider` is the interface a provider extension is expected to follow when it wants to supply searchable memory. `MemoryMatch` represents one result found by that search. `DEFAULT_MEMORY_SEARCH_PROVIDER` points to the default provider name or value used when no special provider is chosen.

The reason this matters is stability. Internal modules can move or change over time, but SDK users need a reliable import location. This file acts like a reception desk: it does not do the work itself, but it tells outside code where to find the official public names. Without it, extension authors might depend on internal paths, making their code more likely to break when the project is reorganized.


### `core/src/ufo/sdk/search.py`

`other` · `cross-cutting import-time API surface`

This file is like a front desk for the search system. The real search definitions live elsewhere, in `ufo.search`, but outside code should not have to know that internal location. Instead, an extension or tool can import search concepts from `ufo.sdk.search`, which is part of the public software development kit, or SDK: the set of names the project deliberately exposes for others to build on.

The file re-exports the main pieces of the search contract. A search provider is a backend that can answer a search query with search results. It may also fetch a page when given a fetch request, returning the fetched page content. If a provider cannot fetch something, it can signal that with `SearchUnsupported`.

The important purpose here is stability. If the internal code is reorganized later, this file can keep the public import path the same. Without it, extension authors might import directly from internal modules, making their code more likely to break when the project changes its structure.
