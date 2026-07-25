# SDK platform service import shims  `stage-16.3`

This stage is shared behind-the-scenes support for people who build on top of the SDK. These files are “import shims”: small doorway modules that give users a stable, public path to approved tools, while hiding where the real code lives inside the project. They do not do the main work themselves. They make the system easier and safer to use from extensions and outside code.

The accounting shim exposes accounting and spending summary objects. The bearer shim exposes only token verification helpers, so extensions can check bearer tokens without gaining access to token creation secrets. The grants shim opens access to grant audit summaries and the function that produces them. The hub shim re-exports hub-related types. The memory shim exposes memory-search types. The observability shim, named o11y, gives access to structured logging, meaning logs with consistent fields machines can read. The objects shim exposes approved object tools. The operator shim provides public access to operator web-session authentication helpers. The seats shim re-exports seat classes and helper functions. Together, these files form a tidy public counter in front of deeper internal shelves.

## Files in this stage

### Accountability shims
Public SDK doorways for accounting summaries, bearer-token verification, and grant-audit reporting.

### `core/src/ufo/sdk/accounting.py`

`data_model` · `cross-cutting: active when SDK users import accounting/spend types`

This module exists to make the public SDK easier and safer to use. Instead of asking outside code to import directly from the internal `ufo.accounting` module, it re-exports the accounting pieces that are meant to be part of the supported public surface. Think of it like a reception desk: the actual work happens in the office behind it, but visitors are given one clear, stable place to ask for what they need.

The objects exposed here describe spending and usage totals, such as reports, totals by subject or dimension, and price digest totals. These are the same kinds of values used when a surface shows a workspace spending rollup through `SurfaceContext.spend_rollup`, and the same sums printed by the `ufoctl spend` command.

The file also re-exports `MICRO_USD_PER_USD`, a conversion constant for money stored in micro-dollars, and `metered_workspaces`, which identifies workspaces that are measured for usage-based billing. Nothing is calculated here. The important job is API shape: it keeps the SDK’s accounting imports named, intentional, and decoupled from the internal package layout.


### `core/src/ufo/sdk/bearer.py`

`util` · `request handling`

This file is a small public doorway into the project’s bearer-token verification tools. A bearer token is like a temporary badge: whoever presents it can prove they were allowed through by the gateway. Surface extensions need to check those badges, but they should not be trusted with the secret key used to make them.

To keep that boundary clear, this SDK file simply re-exports three helpers from `ufo.bearer`: one to verify a token, one to read verified claims, and one to extract the workspace claim. The important design choice is that callers do not pass in the signing secret. The underlying helpers resolve `UFO_TOKEN_SECRET` themselves, so extensions can ask “is this token valid?” without directly handling the key that could mint or forge tokens.

Without this file, extensions might import deeper internal modules directly, making the public API harder to change safely. This file acts like a clearly labeled service window: extensions can validate gateway-issued tokens here, while token creation and secret ownership stay inside the core control plane.


### `core/src/ufo/sdk/grants.py`

`other` · `cross-cutting; active when SDK users import grant-audit helpers`

This file is part of the public SDK, which is the stable area meant for connector code and outside callers. Instead of asking users to reach into the project’s internal `ufo.grants` module, it exposes just the pieces they are meant to use: `GrantSummary` and `grant_summaries`. Think of it like a reception desk: the real work happens elsewhere in the building, but visitors are directed to one clear counter rather than wandering through private offices.

The file does not define new behavior. It imports the grant-audit view from `ufo.grants` and makes it available under `ufo.sdk.grants`. The comments explain why this pattern exists: the SDK keeps its package `__init__.py` empty, so public names live in explicit modules like this one. That keeps the public surface clear and avoids hidden startup code in package initializers.

Without this file, connector authors would either have no approved SDK path for reading grant summaries, or they would need to import internal code directly. That would make their code more fragile, because internal module layouts can change more freely than public SDK modules.


### Platform extension shims
Stable import surfaces for hub types, memory search types, structured logging, and object-related SDK tools.

### `core/src/ufo/sdk/hub.py`

`other` · `import time / public SDK access`

This file is like a signposted front desk for the hub part of the SDK. The real hub code lives elsewhere, mainly in `ufo.hub` and `ufo.models.interface`, but outside code should not need to know those internal paths. Instead, users can import names such as `Hub`, `LiveFrame`, `ToolCall`, or `TextDelta` from `ufo.sdk.hub`.

The comment at the top explains an important project rule: the package keeps `__init__.py` files empty, so public imports are placed in named modules like this one. That means this file exists mostly to shape the public API, which is the set of names the project promises users can rely on.

Nothing is computed here. Each import brings in an existing class, protocol, or data type and immediately exposes it under the same name. For example, `InProcessHub` is made available for users who want the built-in in-process hub backend, while `LiveFrame`, `ToolCall`, `SkillLoad`, and similar names describe events or messages used by hub integrations.

Without this file, users would have to import from deeper internal modules. That would make the SDK harder to learn and would tie user code more tightly to the project’s internal layout.


### `core/src/ufo/sdk/memory.py`

`data_model` · `import time / extension setup`

This file exists to make the project’s memory-search feature easier and safer to use from provider extensions. Instead of asking extension code to import directly from the internal `ufo.memory` module, it re-exports the public pieces under `ufo.sdk.memory`. That creates a cleaner boundary: outsiders can depend on the SDK path, while the project keeps freedom to reorganize its internals later.

The file exposes three names. `MemorySearchProvider` is the interface or shape that a memory-search provider is expected to follow. `MemoryMatch` represents a result found during a memory search. `DEFAULT_MEMORY_SEARCH_PROVIDER` points to the default provider used when no custom one is chosen.

A useful analogy is a reception desk in a large building. The actual offices may be elsewhere, but visitors are told to go through the front desk. Here, `ufo.sdk.memory` is that front desk for memory-search extension code.

There is no logic here beyond importing and re-publishing names. Its value is not computation, but stability and clarity for people building on top of the system.


### `core/src/ufo/sdk/o11y.py`

`util` · `cross-cutting`

This file exists so code outside the core project, especially extensions, can write logs in the same structured way as the rest of the system. Structured logging means log messages are recorded with clear fields, not just plain text, so they are easier to search, filter, and understand later.

The file does not build a new logger of its own. Instead, it imports the existing `log` object from `ufo.o11y` and exposes it again from the SDK namespace. In everyday terms, it is like putting a clearly labeled public service window in front of an internal office: extension code does not need to know where the logging machinery lives inside the project; it can just import it from the SDK.

Without this file, extensions might have to reach into internal modules directly, which makes them more fragile if the project layout changes. By re-exporting `log` here, the project can offer a stable public import path while keeping the real logging implementation elsewhere.


### `core/src/ufo/sdk/objects.py`

`data_model` · `cross-cutting`

This module exists to keep a clean boundary between the project’s public software development kit, or SDK, and its internal implementation. An SDK is the set of names outside code is meant to use. Instead of asking extension authors to import directly from `ufo.objects`, this file re-exports the approved object classes, errors, and constants through `ufo.sdk.objects`.

Think of it like a reception desk in a large office. Visitors should not wander through private rooms to find what they need. They go to the desk, and the desk gives them the right forms and contacts. Here, the “forms and contacts” are names such as `ObjectKind`, `ObjectStore`, `ObjectOwner`, and errors like `UnknownObject` or `VerbNotSupported`.

There is no active logic in this file. It does not create objects, save data, or validate anything itself. Its job is stability and clarity: extensions can depend on this public import path even if the internal layout changes later. The comment also explains why this lives in a named module rather than in `__init__.py`: the project forbids code in package initializer files, so public SDK names are exposed through small modules like this one.


### Administrative service shims
Public SDK re-exports for operator web session helpers and seat-related service tools.

### `core/src/ufo/sdk/operator.py`

`util` · `cross-cutting request handling`

This file is a small bridge between the public SDK area and the deeper operator extension code. The operator surface is a special part of the system meant only for trusted operator tools, such as debug or admin-style pages. Those tools need shared rules for recognizing an operator, choosing the right workspace from a `?ws=` web address parameter, and binding the operator session cookie used across requests.

Rather than make every caller import directly from `ufo.ext.operator`, this file re-exports the important pieces from one clear location: `core/src/ufo/sdk/operator.py`. Think of it like a front desk. The actual work happens in the back office, but visitors are told to come to the front desk so the building can be reorganized later without changing where people go.

The names it exposes are `OPERATOR_COOKIE`, `bind_operator_session`, `operator_bearer`, and `resolve_operator_workspace`. Together, these support operator-only authentication: checking bearer-style credentials, resolving the workspace an operator wants, and attaching the shared cookie that keeps the operator session connected. If this file disappeared, code that depends on this public SDK path would break or would have to know about the internal extension module.


### `core/src/ufo/sdk/seats.py`

`io_transport` · `cross-cutting`

This file is like a labeled shelf at the front of a workshop. The real tools live deeper inside the project, in `ufo.seats`, but extension authors and SDK users should not have to know that internal layout. Instead, they can import seat-related pieces from `ufo.sdk.seats`.

“Seats” here means the project’s way of tracking which members occupy limited account or workspace slots. The exported items include the seat data itself, snapshots of seat state, errors for invalid changes such as reaching a limit or referring to an unknown member, and helper functions for finding a member’s workspaces or the owner conversation.

The important design choice is separation. The core project keeps the actual seat rules in one place, while SDK users get a clean public doorway to those rules. If the internal module layout changes later, this file can preserve the public import path, so outside integrations are less likely to break.

There are no functions defined here. Every name is imported from `ufo.seats` and immediately made available again from this module.
