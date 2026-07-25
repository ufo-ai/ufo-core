# SDK backend and connector integration doorways  `stage-16.2`

This stage is shared support for people who extend the system. It is not where the main work happens. Instead, it provides “front doors” into the codebase: stable import paths that extension authors can rely on, even if the internal folders change later.

Each file opens one door to a different kind of plug-in point. authproxy.py exposes the approved authentication-proxy types, so connectors can receive credentials safely. browser.py exposes browser connection types without revealing the engine’s private browser code. connectors.py gathers the pieces needed to add connector providers, including OAuth, which is the common web sign-in handoff. sources.py gathers tools for adding content sources, including syncing, REST helpers, pagination, and sync results. index.py exposes the types needed to plug in search indexes and embedding backends. search.py re-exports the public search interface for tools that need to ask questions over indexed content. models.py collects model message, client, usage, and helper types. sandbox.py exposes controlled execution tools. Together, these files act like labeled sockets where outside integrations can plug in cleanly.

## Files in this stage

### Auth proxy doorway
Stable SDK imports for extensions that provide credentials to feed-sync connector integrations.

### `core/src/ufo/sdk/authproxy.py`

`other` · `cross-cutting`

This file exists to give extension authors one stable place to import the pieces needed to plug in a credential provider. In this project, a connector may need a credential, such as a token or key, before it can talk to an outside service. An authentication proxy is the extension-side object that supplies that credential.

The file does not implement the credential lookup itself. Instead, it re-exports three names from deeper modules: `AuthProxy`, the interface an extension implements; `Credential`, the value a connector receives and uses to authenticate; and `AuthProxySpec`, the manifest entry that declares the proxy to the system. Think of it like a signposted front desk: the actual offices are elsewhere, but outsiders are meant to enter through this desk so the project can keep its internal layout flexible.

The module docstring also explains the selection rules. If there is only one backend, it is used automatically. If there are several, configuration chooses one. Some providers may be claimed by a broker, and in that case the broker supplies the credential instead of the default backend. Without this public re-export, extension code would need to know internal package paths, making extensions more fragile when the project is reorganized.


### Browser access doorway
Stable SDK imports for extensions that need the public browser-connection types.

### `core/src/ufo/sdk/browser.py`

`io_transport` · `cross-cutting`

This file is a small public doorway into the project’s browser connection system. The real implementations live in `ufo.browser`, but this file re-exports the important names through the `ufo.sdk` package, which is the safer, intended surface for extension code. In everyday terms, it is like a reception desk: outsiders do not need to walk through the whole building to find the right office; they come here and are handed the official contact points.

The exported types describe how the engine gets a temporary connection to a browser using CDP, the Chrome DevTools Protocol, which is a way for software to control and inspect a Chromium-based browser. A `CdpProvider` can create a per-turn `CdpLease`, and that lease gives access to a `CdpEndpoint`, meaning the browser URL and any needed connection headers. When the turn ends, the lease can be released. The file also exposes `SessionGone`, used when a saved browser session can no longer be reattached, and `FindCompleter`, a callback hook for ranking or completing browser element searches.

Nothing new is implemented here. Its value is stability and clarity: extension authors can depend on this SDK path, while the project remains free to organize its internal browser code behind the scenes.


### Connector and source providers
Stable SDK imports for connector providers, OAuth integration, content-source syncing, REST helpers, pagination, and sync results.

### `core/src/ufo/sdk/connectors.py`

`other` · `cross-cutting`

This file does not create new behavior itself. Instead, it acts like a front desk: outside code can come here to find the official connector-related names, without needing to know where those names live inside the project.

Connectors are the system’s way to let an extension offer access to an outside service through a broker. In plain terms, a broker is an adapter that knows how to list available tools, run those tools on the server side, work with files or search, and supply credentials for feed syncing. OAuth is the sign-in flow that lets a user grant access to an outside service without handing over a password.

The docstring explains the larger design. An extension provides an OAuth provider for the sign-in handoff and a connector broker for the actual service features. Core code then drives the user through `/connect`, merges all available connectors into a registry, puts that registry into the tool context for a turn, and routes feed-sync credentials through it. Because this file re-exports the public shapes from `ufo.connectors` and `ufo.grants`, extension code can depend on `ufo.sdk.connectors` as the stable seam. If this file were removed, integrations might have to import internal modules directly, making them more fragile when the project is reorganized.


### `core/src/ufo/sdk/sources.py`

`other` · `cross-cutting, when source extensions import the SDK`

This file does not create new behavior of its own. Its job is to make the source-extension API easy and safe to use. Instead of asking extension authors to know many internal module paths, it re-exports the important building blocks from the deeper `ufo.sources` packages.

A content source is something that can fetch outside records, such as documents, tickets, channels, or repository items, and turn them into `Page` objects the system can sync. Extension authors usually implement `SourceBackend`, or use the REST connector framework when the outside service is reached through HTTP calls. This file exposes both paths.

It also exposes the sync vocabulary: cursors for resuming later, `SyncResult` for reporting what was fetched or deleted, and special exceptions such as `CursorExpired` and `StreamSkipped`. These let an extension tell core whether a run should restart from scratch, skip cleanly, tombstone old pages, or keep existing data untouched.

Think of this file like the labeled tool shelf in a shared workshop. The tools live elsewhere, but this shelf is where builders are expected to pick them up. That matters because it keeps extension code from depending directly on private internal layout, making the SDK easier to learn and less likely to break when internals move.


### Index and search backends
Stable SDK imports for extensions that plug in indexing, embedding, and search-related backend interfaces.

### `core/src/ufo/sdk/index.py`

`other` · `cross-cutting`

This file does not define new behavior. Instead, it acts like a front desk for the project’s indexing system. Extensions can provide their own index backend, which is the part that stores text chunks and finds matching results later, including both keyword-style search and vector search. Vector search means comparing numeric representations of text, called embeddings, so similar meanings can be found even when the words differ.

The actual classes and constants live in `ufo.indexing`, but this file exposes them through `ufo.sdk.index`. That matters because extension authors should not need to know the internal layout of the core package. They can depend on this SDK module as the stable contract.

The exported pieces describe the main indexing workflow: text is split into `Chunk`s, an `EmbedClient` turns those chunks into embeddings, an `IndexBackend` stores and searches them, and searches return `Hit`s. Constants such as `OWNER_KIND_PAGE` and `OWNER_KIND_MEMORY_ITEM` label what kind of thing a chunk came from. `IndexScope` tells the backend what area of data to delete or operate on. Without this file, extension code would have to import internal modules directly, making it more fragile if the core project reorganizes its internals.


### `core/src/ufo/sdk/search.py`

`data_model` · `cross-cutting`

This file is a small public doorway into the search system. The real search definitions live in `ufo.search`, but outside code is meant to reach them through `ufo.sdk.search`. That matters because extensions should not have to know where the project keeps its internal code. They can import the official SDK path instead, which is less likely to change.

The search system has two sides. A search extension implements a `SearchProvider`, which is the plug-in-like piece that can answer search requests. A tool or caller creates a `SearchQuery` to ask for results, receives `SearchResults` made of `SearchHit` entries, and may later use a `FetchRequest` to retrieve a full page as a `FetchedPage`. If a provider cannot fetch something, it can raise `SearchUnsupported`, which is a clear way to say, “this provider does not support that operation.”

An everyday analogy is a service counter with a labeled public window. The work happens behind the wall, but customers use the window marked “search.” This file is that window: it keeps the public contract neat while allowing the internal layout to remain hidden.


### Model backend doorway
Stable SDK imports for approved model clients, message types, usage records, and model helper functions.

### `core/src/ufo/sdk/models.py`

`data_model` · `cross-cutting import time`

This file exists to create a clean boundary between outside code and the project’s internal layout. In plain terms, it is like a front desk: extensions can ask here for the model tools they are allowed to use, without needing to know which back office each tool actually lives in.

It does not define new behavior of its own. Instead, it re-exports important building blocks from elsewhere in the codebase. These include model client classes for Anthropic and OpenAI, shared message and content block shapes, streaming event types such as text changes and tool-call updates, image trimming helpers, price information, and usage accounting records.

The important reason for this file is stability. If an extension imports directly from deep internal modules, a future folder move or refactor could break it. By importing from `ufo.sdk.models`, outside code depends on the public SDK surface instead. The project can then reorganize internals while keeping this file’s names compatible.

There are no functions in this file. Its whole job happens when Python imports it: it gathers selected names and makes them available as the official model-facing API.


### Sandbox tools doorway
Stable SDK imports for sandbox types, constants, and helper functions used by extension authors.

### `core/src/ufo/sdk/sandbox.py`

`other` · `cross-cutting import-time SDK access`

This module does not create new behavior of its own. Its job is to collect and re-export the pieces that an outside extension needs in order to work with UFO sandboxes. A sandbox is an isolated workspace where code can run with controlled files, mounts, and connection details. Extension authors use these exported names to describe what kind of sandbox backend they provide, receive sandbox session objects, and work with mounted storage such as S3-backed file systems.

The file exists because `ufo.sdk` is meant to be the public face of the project. The internal code lives in modules like `ufo.sandbox.session`, `ufo.sandbox.fs_mount`, `ufo.ext.manifest`, and `ufo.blob`, but outsiders should not have to import those directly. This is like a reception desk: the actual offices are elsewhere, but visitors get the right forms and contacts from one predictable place.

It re-exports sandbox session types such as `SandboxSession`, `SandboxSpec`, `SandboxHandle`, and `ExecResult`; extension-facing contracts such as `Carrier` and `CarrierSpec`; mount helpers such as `s3fs_command`, `mount_scripts`, and `mount_health_check`; and shared constants such as `WORKSPACE_DIR` and mount timeout values. If this file were missing, extension code could still reach the internals, but it would become more fragile because internal paths could change.
