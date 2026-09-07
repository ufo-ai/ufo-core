# SDK service, content, and runtime resource facades  `stage-19.3`

This stage is shared behind-the-scenes support for people building on top of the system. It is not where the main work is performed. Instead, it provides stable “front doors” in the SDK, so extension authors can import approved tools without depending on the project’s private folder structure. These files mostly re-export existing runtime pieces, meaning they point to real implementations elsewhere rather than adding new behavior.

The audience module exposes conversation-audience tools. Browser exposes the allowed browser connection interface. Hub gathers hub classes and event-frame types. Index lets extensions plug in search and embedding backends, while search exposes the general search interface. Listings provides helpers for returning long lists one page at a time. Memory exposes memory-search types. Models collects model clients, message formats, tool-call types, pricing records, and permission helpers. Objects exposes object kinds and helper classes. Sandbox gathers tools for controlled execution areas. Terminal exposes approved terminal and blob-store types. Together, these facades act like a clean control panel over deeper machinery.

## Files in this stage

### Interaction entry points
Public facades for conversation audience, browser transport, and hub integration types.

### `core/src/ufo/sdk/audience.py`

`util` · `cross-cutting`

In this project, an “audience” means who a conversation turn is meant for or visible to: everyone in a shared space, a specific room, a foreign room, or particular audience members. The real audience rules live deeper in the runtime layer, under `ufo.runtime.turns.audience`. This file does not create new rules of its own. Instead, it re-exports the important audience names so outside users can import them from the SDK layer.

That matters because the SDK is the friendlier, stable surface of the project. Without this file, callers would need to know the internal runtime path, which is like asking library users to enter through the staff-only back door. By exposing `Audience`, constants such as `SHARED_AUDIENCE`, and helper functions such as `parse_audience` and `readable_audiences`, this file keeps public code cleaner and gives the project freedom to reorganize its internals later.

In short, it is a small boundary file. It connects the public SDK namespace to the internal audience implementation while preserving the exact imported names.


### `core/src/ufo/sdk/browser.py`

`io_transport` · `cross-cutting`

This file does not implement browser behavior itself. Instead, it re-exports a small set of browser-connection building blocks from the internal `ufo.browser` package and presents them as part of the public SDK. In everyday terms, it is like a front desk: outsiders come here for the approved forms and names, while the real machinery stays behind the counter.

The types it exposes describe how an extension can provide or reconnect to a Chrome DevTools Protocol connection. Chrome DevTools Protocol, or CDP, is the control channel that lets software drive a browser: open pages, inspect elements, click things, and so on. A `CdpProvider` creates a temporary `CdpLease` for a turn of work, and that lease gives the engine a `CdpEndpoint`, meaning the browser URL and any headers needed to connect. The lease can also carry a durable token so a later run can reattach to the same browser session if it still exists. If the session is gone, `SessionGone` signals that clearly.

The file also exposes helper concepts such as `FileBytes`, used when a remote browser needs access to a local workspace file, and `FindCompleter`, a callback hook for ranking page elements. Without this file, extensions would either need to import internal modules directly or would lack a stable, documented browser seam to build against.


### `core/src/ufo/sdk/hub.py`

`other` · `cross-cutting import/public API use`

This file is like a clearly labeled front desk for the hub part of the SDK. The actual implementation lives elsewhere, mostly in `ufo.runtime.hub`, but users of the SDK should not need to know that internal layout. Instead, they can import names such as `Hub`, `InProcessHub`, `LiveFrame`, `Reply`, or `Terminal` from `ufo.sdk.hub`.

The hub appears to be the system’s live communication channel: it uses “frames,” meaning structured messages that describe things happening during a run, such as activity updates, queued arrivals, cost changes, replies, parking and resuming work, or terminal completion. This module also exposes `TextDelta`, a type for streamed text changes, from the harness model interface.

The reason this file matters is stability. Internal files can move or be reorganized, but this SDK module can remain the public doorway. Without it, extension authors or client code would have to import directly from deeper runtime modules, making their code more fragile. The comments also explain an important project rule: package `__init__.py` files stay empty, so public SDK names live in explicit modules like this one.


### Content discovery facades
Stable SDK import paths for indexing, paged listings, memory lookup, and search interfaces.

### `core/src/ufo/sdk/index.py`

`other` · `cross-cutting`

This file exists to draw a clean line between the core system and outside extensions. In plain terms, it tells plugin authors: “If you want to provide a search index or text embedding service, import the needed building blocks from here.” Without this file, extensions would have to import directly from `ufo.runtime.indexing`, which is more internal and could make the project harder to change safely.

The main ideas it exposes are chunks of text, search hits, index scopes, and backend interfaces. A “backend” here means a replaceable implementation, like choosing which search engine or embedding provider the system should use. An embedding is a numeric representation of text that lets software compare meaning, not just exact words.

At startup, core code can select an extension-provided index backend using configuration such as `memory.index_backend`, and an embedding backend using `memory.embed_backend`. This SDK file does not build or run those backends itself. It simply re-exports the official names that extensions should implement or use. Think of it like a labeled socket on a machine: the real wiring is inside, but outsiders get a safe, clearly marked place to plug in.


### `core/src/ufo/sdk/listings.py`

`data_model` · `used when extension code imports SDK listing helpers, especially during portal listing request handling`

When an extension needs to answer a portal listing, it often cannot or should not return every possible item at once. Instead, it returns a page of results plus a cursor, which is like a bookmark telling the next request where to continue. This file is the SDK-facing doorway for that idea.

The actual paging logic lives in `ufo.runtime.listings`. This file imports the important pieces from there and exposes them under `ufo.sdk.listings`, so outside extension code can depend on a clean public API instead of reaching into the runtime internals. That separation matters because the project can reorganize its private runtime code later while keeping the SDK import path steady for users.

The exported names are `ListingCursor`, `ListingPage`, `MalformedCursor`, `page_of`, and `page_query`. Together, they let extensions understand incoming paging requests, build a page of listing results, and report when a supplied cursor is invalid. Think of this file like a reception desk: it does not do the work itself, but it points users to the right official tools through a simple, supported entrance.


### `core/src/ufo/sdk/memory.py`

`data_model` · `cross-cutting import time`

This file does not implement memory search itself. Instead, it re-exports a few important names from the internal runtime memory module so that outside code can use them through the SDK, which is the project’s public interface for extensions.

In plain terms, it is like a reception desk. The real work happens elsewhere, but this file tells plugin or provider authors, “come through this door if you need memory-search tools.” That matters because external extensions should not have to know the project’s internal folder layout. If the runtime code moves later, this SDK file can keep offering the same import path.

The three exported items are the default memory-search provider name or object, the `MemoryMatch` type that represents a found memory result, and the `MemorySearchProvider` type that describes what a provider must look like. By importing and immediately re-exporting them, this file makes those runtime concepts part of the supported extension API without adding extra behavior or transformation.


### `core/src/ufo/sdk/search.py`

`other` · `cross-cutting`

This file is a small public doorway into the project’s search system. Its job is to make the search API easy and safe to find from the SDK package, instead of making outside code reach into the deeper runtime package directly.

In practical terms, an extension that wants to provide search results needs to know the shapes of the requests and responses it must work with. Those shapes include things like a search query, a list of search hits, a request to fetch a page, and the fetched page result. This file imports those names from `ufo.runtime.search` and exposes them again under `ufo.sdk.search`.

The reason this matters is stability. Think of it like a front desk in a building: visitors should go to the front desk, not wander through staff-only hallways looking for the right person. By importing from `ufo.sdk.search`, extension code depends on the public SDK surface, while the project can keep its internal runtime layout more flexible.

There are no functions here and no behavior runs at request time. The file only defines what names are publicly available for search integrations.


### Model and runtime resources
Public SDK gateways for model APIs, object abstractions, sandbox tools, and terminal resources.

### `core/src/ufo/sdk/models.py`

`data_model` · `cross-cutting import-time API surface`

This file does not define new behavior. Its job is to gather the model-facing parts of the system and re-export them as a clean public API. Think of it like a reception desk: instead of sending visitors through many private hallways, it gives them one official counter where they can ask for the pieces they need.

The pieces exposed here cover several areas. There are client classes for talking to providers such as Anthropic and OpenAI. There are message and content block types, such as text, images, tool calls, tool results, and streaming events, which describe the shape of conversations with a model. There are model specification and pricing types, which describe what a model supports and what it costs. There are also grant and environment-variable helpers for provider authentication, plus retry and interruption errors that callers may need to react to.

The important point is stability. Code outside the core project can import from `ufo.sdk.models` without knowing where each item really lives inside `ufo.harness` or `ufo.schema`. If the internal layout changes later, this file can be updated while outside extensions keep using the same public import path.


### `core/src/ufo/sdk/objects.py`

`other` · `cross-cutting import-time SDK surface`

This file does not create new behavior. Instead, it collects many object-related names from deeper parts of the project and re-publishes them in one stable place: `ufo.sdk.objects`. Think of it like a front desk in a large building. Visitors should not need to know which back office holds each form; they go to the front desk and get the official version there.

That matters because extensions are meant to build on the public SDK, not on private internal paths that may change. The file exposes constants for built-in object kinds, such as artifacts, conversations, credentials, members, surfaces, workspaces, and agents. It also exposes important object model pieces, such as object references, ownership rules, list pages, store interfaces, action views, and errors like `AdminRequired`, `UnknownObject`, and `VerbNotSupported`.

The repeated `as Name` imports are intentional. They make clear that these names are being exported as part of the public surface, under the same names. There is also a note explaining why this lives in a named module instead of the package `__init__.py`: this project forbids code in `__init__.py` files, so public SDK entry points are split into explicit modules like this one. Without this file, extension code would either have to import from scattered internal modules or lose access to the official object API.


### `core/src/ufo/sdk/sandbox.py`

`other` · `cross-cutting import-time public API surface`

This module does not define new behavior of its own. Instead, it re-exports sandbox building blocks from deeper inside the project under the public name `ufo.sdk.sandbox`. Think of it like a reception desk: the actual workers are in other rooms, but outsiders can come to one predictable place and ask for what they need.

The sandbox is the controlled environment where code can run with known paths, users, proxy settings, browser locations, and file boundaries. This file exposes constants such as sandbox paths and environment names, value objects such as `SandboxSpec` and `ExecResult`, protocol-style types such as `Carrier`, and error types such as `SandboxUnreachable`. It also exposes helper functions for safe path handling, workspace paths, proxy environment setup, shipped app information, and locating the client binary.

The comment at the top explains why this matters: extensions register a `CarrierSpec` and implement the `Carrier` protocol. In plain terms, a deployment can swap out the backend that actually provides the sandbox, while extension code keeps talking to the same public interface. Without this file, extension authors would need to import from internal modules directly, making their code more fragile whenever the internal structure changes.


### `core/src/ufo/sdk/terminal.py`

`io_transport` · `cross-cutting`

This file does not define new behavior. Its job is to gather and re-export the pieces an external terminal transport extension is allowed to use. A terminal transport is the layer that lets the system talk to a terminal-like workspace, such as sending operations to it or noticing when it has disappeared. The file also exposes `BlobStore`, the shared place used to store and retrieve larger chunks of data, plus `BlobNotFound`, the error used when requested data is missing.

The reason this file matters is stability. The real implementations live deeper in the project, under modules like `ufo.harness.sandbox.terminal` and `ufo.blob`. If outside code imported those internal paths directly, future reorganizing would break users. This SDK module acts like a front desk: callers ask here for `TerminalTransport`, `Terminals`, `TerminalWorkspace`, and related errors and timing constants, while the project remains free to move the back rooms around later.

It also follows a project rule mentioned in the header comment: `ufo.sdk` uses named modules like this one as its public surface, because package `__init__.py` files are kept empty. So this file is intentionally simple, but important: it marks which terminal tools are part of the supported public API.
