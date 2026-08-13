# SDK Hub, Object, Listing, and Surface Facades  `stage-22.5`

This stage is shared support for people building on top of UFO, not part of the main work loop itself. It provides “facades”: simple public doorways that hide the project’s internal file layout. That matters because outside extensions can import stable SDK paths even if the code inside the project is reorganized later.

The hub module is the doorway for hub records and related model types. It does not create new behavior; it simply points users to the approved hub-related names. The listings module does the same for listing and paging helpers, which are tools for returning many items in manageable chunks, like pages in a catalog. The objects module gathers public object helpers and types so extension authors do not need to know where those pieces live internally. The surfaces module is for surface extensions: integrations that show UFO conversations or actions in another user interface. It collects the classes, errors, helper functions, and records those integrations need. Together, these files act like a clean front desk for the SDK.

## Files in this stage

### Public SDK Facades
Stable SDK import doorways for hub records, listing helpers, object helpers, and surface-extension integrations.

### `core/src/ufo/sdk/hub.py`

`data_model` · `import time`

This file is like a clearly labeled front desk for hub features. The real implementations live deeper inside the project, mostly in `ufo.hub`, but users of the SDK should not have to know those internal paths. Instead, they can import the important hub pieces from `ufo.sdk.hub`.

The hub appears to be the part of the system that works with live frames of activity, tool calls, terminal output, cost ticks, parked states, and skill loading. This module collects those public building blocks and exposes them under one friendly SDK module name.

A small design choice matters here: the package has an empty `__init__.py`, because this project forbids putting code there. That means public SDK names are placed in explicit modules such as this one. Without this file, extension authors or SDK users would need to import directly from internal modules, which would make their code more tightly tied to the project’s internal layout. By re-exporting the names here, the project can present a cleaner public API while keeping room to reorganize internals later.


### `core/src/ufo/sdk/listings.py`

`data_model` · `cross-cutting`

Portal listings often need paging: instead of sending every item at once, an extension returns one page of results plus a cursor that says where the next page should start. This file exists so extension code can use those paging building blocks through the SDK namespace, without needing to know where the underlying implementation lives.

It does not define new behavior itself. Instead, it imports selected names from `ufo.listings` and exposes them again: `ListingCursor`, `ListingPage`, `MalformedCursor`, `page_of`, and `page_query`. In plain terms, this is like a front desk that points callers to the right internal office while keeping the public address simple and stable.

That matters because SDK users should not have to depend on internal module layout. If the project later reorganizes where listing logic lives, this file can keep the public import path working. Without it, extensions might import deeper internal modules directly, making them more likely to break when the codebase changes.


### `core/src/ufo/sdk/objects.py`

`other` · `import time / extension development`

This module is a public doorway into UFO’s object system. Instead of asking extensions to import directly from internal modules like `ufo.objects`, `ufo.agents`, or `ufo.conversations`, it re-exports the approved names from one SDK path: `ufo.sdk.objects`.

That matters because extensions are written outside the core project. If they reached into internal files directly, a future refactor could break them even if the public behavior stayed the same. This file works like a shop counter: the storage room behind it may be rearranged, but customers still ask at the same counter.

The names exposed here include object identity and ownership types, list and page shapes, object store interfaces, errors such as “admin required” or “verb not supported,” and a few related constants and helper functions. There is no new business logic in this file. Its job is to make the public contract clear: extensions should register object kinds and write object stores using these exported names, not private core internals.

A notable detail is that `ufo.sdk` keeps its package initializer empty, so public SDK surfaces live in named modules like this one rather than in `__init__.py`.


### `core/src/ufo/sdk/surfaces.py`

`other` · `cross-cutting`

This file does not define new behavior. Instead, it works like a clearly labeled shelf in a toolbox: extension authors can import the tools they need from `ufo.sdk.surfaces` without knowing the deeper internal module layout of the project.

A “surface” is an extension point where UFO can talk to users or outside systems through a particular channel. To build one, an extension registers a `SurfaceSpec`, which describes its `SurfaceRoute`s, meaning the routes or actions the surface supports. Durable surfaces can also provide a two-step writeback flow, represented by `Writeback`, for safely returning results and shared files or artifacts.

The file re-exports privileged context types such as `SurfaceContext`, identity and authentication helpers, conversation and installation summary views, transcript-related records, credential request types, and errors that surface code may need to report clear failure cases. It also exposes small helper functions for working with member messages and transcript access.

This matters because it creates a stable public API. Internal files can move or change, but extension authors can keep importing from this one named SDK module. The comment also explains why this exists as a normal module instead of package-level imports: project rules forbid executable code in `__init__.py`, so public SDK entry points live in explicit files like this one.
