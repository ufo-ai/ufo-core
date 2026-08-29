# Public SDK facades for platform utilities and administration  `stage-19.2`

This stage is shared behind-the-scenes support for extension authors and other outside code. Its job is to provide stable “front doors” under ufo.sdk, so callers do not have to reach into internal folders that may change. Most files here do not invent new behavior. They re-export, meaning they pass along selected tools from deeper modules under safer public names.

The billing doors are accounting for spend reports and balance for prepaid balance tools. seats exposes seat management helpers, while flags exposes the feature-flag check, a simple way to ask whether an optional feature is enabled. audience and subjects provide the standard ways to describe who can see conversation data. untrusted provides the marker used to fence off text from outside sources. delivery_register shares prompt constants used when building delivery-register prompts. hub, listings, and objects publish common SDK types for hubs, paged listings, and stored objects. o11y, short for observability, offers approved logging and metrics. scheduled_fire exposes helpers for cron-like scheduled triggers. operator publishes operator-only session/authentication tools. surface_token exposes token creation and checking for surfaces.

## Files in this stage

### Commercial controls
Public facades for spend reporting, prepaid balance, feature gating, and seat management.

### `core/src/ufo/sdk/accounting.py`

`data_model` · `cross-cutting`

This file does not calculate spending itself. Instead, it re-exports selected accounting objects from `ufo.billing.accounting`, which is where the real billing definitions live. A re-export is like putting commonly needed tools on the front counter of a shop: the tools may be stored in the back room, but visitors can reliably pick them up from one clear public place.

The objects exposed here describe workspace spending: totals by dimension, member and agent spend reports, usage exports, and the conversion constant for micro-dollars. These are the same kinds of values used when a surface shows `SurfaceContext.spend_rollup` or when the `ufoctl spend` command prints a spend summary.

This matters because `ufo.sdk` is meant to be the stable public interface for SDK users. If callers had to import directly from `ufo.billing.accounting`, internal reorganizations could break them. By importing through this file, users get a named, intentional API surface. The file also makes clear which accounting pieces are meant to be public and which remain internal details.


### `core/src/ufo/sdk/balance.py`

`other` · `cross-cutting SDK import`

This file is like a labeled service window for the billing balance part of the system. The real work lives in `ufo.billing.balance`, where the rules for prepaid balances, credits, purchases, headroom, and auto top-ups are defined. This SDK file re-exports those names so outside extensions can import them from a safer public path instead of reaching directly into the internal billing module.

That matters because extensions may need to read a workspace’s prepaid balance, show billing information, credit the balance when a payment settles, or configure automatic top-ups. By gathering those imports here, the project can present a clear public contract: “these are the balance-related tools extensions may use.”

There is no extra behavior in this file. Importing `Balance`, `Purchase`, `credit`, `read_balance`, and the other names from here gives callers the same objects and functions from the core billing module. The repeated `as Name` style makes the re-export explicit, which helps tools and readers see that these names are intentionally part of the SDK surface.


### `core/src/ufo/sdk/flags.py`

`util` · `cross-cutting`

This file is a small doorway between the project’s internal feature-flag system and the public SDK surface. A feature flag is a named on/off switch used to enable, hide, or gradually roll out behavior without changing every caller. Instead of making extensions import directly from the project’s internal `ufo.flags` module, this file re-exports `flag_enabled` from a safer public location: `ufo.sdk.flags`.

The important point is not that this file contains complex logic. It does not. Its job is to protect the shape of the public API. Think of it like a reception desk: visitors do not need to know which office actually keeps the records; they just go to the same desk every time. If the internal flag system moves later, this SDK file can keep the public import path the same.

Without this file, extension authors might depend on internal module paths. That would make future refactoring harder, because changing internals could break outside users. This file keeps the boundary clean: extensions can ask whether a gated feature is available, while the project remains free to organize its internal code.


### `core/src/ufo/sdk/seats.py`

`other` · `import time / SDK use`

This module is like a clearly labeled shelf in a workshop. The real tools live elsewhere, in `ufo.seats`, but this file makes the seat-related ones easy and safe for outside code to find through `ufo.sdk.seats`.

The problem it solves is public API clarity. Extensions should not need to know the internal layout of the core package. They can import `Seats`, `SeatEntry`, and helper functions such as `member_by_email` or `member_is_admin` from this SDK-facing module instead. That gives the project room to reorganize its internal files later without forcing every extension to change its imports.

The objects re-exported here describe or inspect seat state: who has a seat, what workspaces a member belongs to, whether a member is an admin, and what domain a workspace uses. The important point is that the rules and validation still belong to the core seat module. This file only exposes those rules at the boundary where extension code is expected to interact with them.

There are no functions or classes created here. Its value is in being a stable doorway into the seat subsystem.


### Audience and visibility
Stable imports for conversation audiences, subject visibility labels, and untrusted-text marking.

### `core/src/ufo/sdk/audience.py`

`data_model` · `cross-cutting import-time API exposure`

This file exists to make the project easier to use from the outside. Instead of asking users to know that audience logic lives under `ufo.turns.audience`, it re-exports the important names through `ufo.sdk.audience`, which is a clearer public location for SDK consumers.

An “audience” here means who a conversation turn is meant for or visible to, such as a shared room, a specific room, a foreign room, or individual audience members. The actual rules and helper functions are not implemented in this file. They are imported from the internal audience module and exposed again under this SDK-facing module.

This is like a shop counter: the goods are stored in the back room, but customers should not need to know the storage layout. They come to the counter and get the stable items they need. That matters because internal code can move around later while outside users keep importing from the same public path.

If this file were removed, code that depends on the SDK audience API could break even though the real audience logic still exists elsewhere. Its value is stability, clarity, and keeping a clean boundary between public API and internal implementation.


### `core/src/ufo/sdk/subjects.py`

`data_model` · `cross-cutting`

This is a small public doorway into the project’s subject system. In this codebase, a “subject” means an audience: for example, something shared with the whole workspace, or something visible to a specific member. Other parts of the system use these subject labels when deciding whether a row of data, or a conversation audience, is tied to a shared source or to an individual thread.

The file does not define new behavior itself. Instead, it re-exports a few names from the deeper internal module `ufo.turns.subjects`. That means outside code can import `SHARED_SUBJECT`, `MEMBER_SUBJECT_PREFIX`, `member_subject`, and `subject_shared` from the SDK path without needing to know where the internal implementation lives.

The practical value is stability and clarity. It is like a front desk that points callers to the right office: users of the SDK get one simple address, while the project remains free to reorganize its internal files later. Without this file, callers might depend directly on internal modules, making future changes more likely to break them.


### `core/src/ufo/sdk/untrusted.py`

`util` · `cross-cutting`

This file is a small bridge between the public SDK area and the core turn-handling code. Its job is to expose one shared definition called `wall`, imported from `ufo.turns.untrusted`. That `wall` represents the boundary used for content the system should not fully trust, such as text produced by an external probe, a provider, or another agent.

The idea is like putting questionable papers into the same clearly labeled folder no matter who received them. If an extension needs to pass along outside output, it can use this SDK import and mark that output the same way the core system marks tool results and subagent hand-backs. Without this file, extension authors might invent their own markers or import from deeper internal modules, which could lead to inconsistent safety boundaries.

There is no extra behavior here. The file deliberately re-exports the core `wall` object under the SDK namespace, so callers have a stable, simple place to get it.


### Delivery and content helpers
SDK doorways for delivery-register prompt constants and shared hub, listing, and object helper types.

### `core/src/ufo/sdk/delivery_register.py`

`other` · `cross-cutting: used when extensions or prompt-building code need the public delivery-register constants`

This module is like a labeled shelf in a public toolbox. The real delivery-register text lives elsewhere, inside `ufo.turns.delivery_register`, but outside code should not have to know that internal path. Instead, extensions can import it from `ufo.sdk.delivery_register`, which is part of the project’s public software development kit, or SDK: the supported set of tools other code is meant to use.

The delivery register is a block of prompt text that gets prepended to direct model calls so those calls write their results into the same shared register used by the shell and by subagent prompts. In plain terms, it helps different parts of the system leave their answers in the same agreed-upon place, rather than each writing notes in a different notebook.

The file also re-exports the description used for subagent results. This keeps prompt wording consistent across the system. The comment explains an important design rule: `ufo.sdk` uses small named modules like this one because package `__init__.py` files are kept empty. Without this file, extension code would either need to import from internal modules directly, which is more fragile, or duplicate prompt text, which could drift out of sync.


### `core/src/ufo/sdk/hub.py`

`other` · `cross-cutting import-time public API`

This file is like a labeled shelf at the front of a workshop. The real tools live deeper inside the project, but users of the SDK should not need to know exactly which internal drawer each tool comes from. Instead, they can import hub concepts from `ufo.sdk.hub`.

The “hub” appears to be the part of the system that carries live activity frames: messages such as arrivals, replies, parked work, resumed work, terminal states, activity updates, and cost ticks. This file re-exports those public names from `ufo.hub`, plus `TextDelta` from `ufo.models.interface`, so extension authors can build against the intended public interface.

This matters because the package deliberately keeps `__init__.py` empty. In Python, `__init__.py` often exposes a package’s public API, but this project avoids putting code there. So named modules like this one become the official import locations.

Nothing is transformed here. Importing from this file is the same as importing the listed objects from their original modules, but with a cleaner and more stable SDK-facing path. If this file were missing, external hub extensions might have to depend on internal module paths, making them more fragile when the project is reorganized.


### `core/src/ufo/sdk/listings.py`

`other` · `cross-cutting`

This file is a small public doorway into the project’s listing system. A “listing” here means a paged set of results, like showing search results one screen at a time instead of all at once. Paging matters because portals or extensions may need to return many items, and sending them in controlled chunks is faster, safer, and easier for callers to navigate.

The actual listing logic lives elsewhere, in `ufo.listings`. This SDK file imports selected names from that internal module and exposes them under `ufo.sdk.listings`. That matters because outside extension code can depend on the SDK path without needing to know where the project keeps its internal implementation. It is like a shop counter: the goods may be stored in the back room, but customers use the counter because it is the agreed public place.

The exported pieces include cursor and page types, an error for bad cursors, and helper functions for creating or reading paged results. If the internal module moves or changes, this file can preserve the public import path for extensions. Without it, extension authors might import internal modules directly, making their code more likely to break when the project is reorganized.


### `core/src/ufo/sdk/objects.py`

`data_model` · `cross-cutting import-time public API`

This file does not define new behavior. Instead, it gathers many object-related building blocks from deeper parts of the codebase and re-exports them under one public SDK module. Think of it like a front desk: visitors do not need to know which back office owns each form; they can ask at one clear place.

The objects exposed here describe the kinds of things the system can work with, such as agents, artifacts, conversations, credentials, members, surfaces, and workspaces. It also exposes shared object concepts such as object references, owners, list pages, store interfaces, permission-related views, and errors like “admin required” or “verb not supported.”

The opening comment explains the reason for this pattern. Extensions should build against the SDK surface, not internal modules that may change. Also, this project keeps package `__init__.py` files empty, so public imports live in named modules like this one rather than at the package root.

Without this file, extension code would have to import from many internal locations. That would make extensions more fragile, because a refactor inside the core package could break outside users even if the public idea stayed the same.


### Operational utilities
Public utility facades for observability and scheduled-fire key handling.

### `core/src/ufo/sdk/o11y.py`

`util` · `cross-cutting`

This file is a public wrapper around UFO's observability tools. Observability means the clues a running system leaves behind, such as logs and metrics, so people can understand what happened later. Instead of making extensions import from the deeper internal module `ufo.o11y`, this file re-exports the safe public pieces under the SDK path.

The important idea is control. Extensions can call functions such as `log`, `warn`, `emit_metric`, and `turn_profile`, but the actual metric registry stays in core. That means an extension can only emit a metric name the core system already knows about. If it tries to invent a new one, it fails loudly instead of quietly creating an untracked metric. This keeps the system's metric list like a shared scoreboard with fixed labels, rather than a wall where every extension can scribble new counters.

There is no new behavior implemented here. Its job is to define a stable public surface: extension code imports from `ufo.sdk.o11y`, while the real work remains centralized in `ufo.o11y`. That separation matters because it lets the project change internal organization later without breaking extension authors.


### `core/src/ufo/sdk/scheduled_fire.py`

`util` · `cross-cutting`

This file is a small public doorway. The real scheduled-fire logic lives elsewhere, in `ufo.ext.scheduled_fire`, but users of the SDK should not have to know that internal path. Instead, they can import from `ufo.sdk.scheduled_fire`, which is a cleaner and more stable public address.

Scheduled fires are runs that happen on a schedule, like a cron job. To track them, the system needs a consistent “admission key”: a structured identifier that says which scheduled task a run belongs to. Think of it like a luggage tag for a scheduled run. One helper builds that tag, and the other reads the tag later to find the original task.

Without this file, outside code would need to import these helpers from the extension module directly. That would leak an internal layout detail and make future refactoring harder. With this file, the project can move or reorganize the implementation later while keeping the SDK-facing import path the same.


### Administrative sessions
Stable SDK imports for operator-only sessions and surface-token authentication helpers.

### `core/src/ufo/sdk/operator.py`

`other` · `request handling and cross-cutting operator tooling`

This file is like a clearly labeled front desk for operator-only web tools. Operator tools are special pages or endpoints meant only for trusted operators, not ordinary users. To keep those tools from each importing authentication pieces from scattered internal locations, this file gathers the public names they need in one place.

It re-exports three things from `ufo.ext.operator`: `FleetDirectory`, `bind_operator_session`, and `resolve_operator_workspace`. In plain terms, these cover the shared directory where operator surfaces can register or look themselves up, the logic that ties an operator session to the shared session cookie, and the resolver that checks an operator request and figures out which workspace is being asked for through the `?ws=` query value.

The important point is stability and consistency. If every debug or operator surface uses this file as its import path, they all rely on the same gatekeeping rules and session behavior. Without this kind of public wrapper, those surfaces could drift apart, import deeper private modules directly, or become harder to change safely later.


### `core/src/ufo/sdk/surface_token.py`

`other` · `cross-cutting import-time API exposure`

This module is like a labeled doorway into a deeper part of the project. The real work of creating and checking surface tokens lives in `ufo.auth.surface_token`, but callers who use the public SDK should not need to know that internal path. Instead, they can import `mint_surface_token` and `verify_surface_token` from this named SDK module.

A surface token is described here as a permanent link address that a “surface” can mint and verify without directly holding the deployment’s secret token key. In plain terms, it lets one part of the system create and validate special links while keeping the most sensitive secret somewhere else.

The file exists because this project keeps `__init__.py` files empty. That means the SDK’s public API cannot be gathered in package initializer files, so each public feature gets its own small module like this one. If this file were removed, SDK users would either lose the documented import path or be forced to reach into internal authentication modules, which would make the system harder to use and easier to accidentally couple to private code.
