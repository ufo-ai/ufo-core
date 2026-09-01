# Public SDK Governance, Identity, and Domain Facades  `stage-18.4`

This stage is shared behind-the-scenes support for people building on top of the system. It creates stable public SDK doorways, so extension authors can import trusted names from ufo.sdk without depending on the project’s private internal layout. Most files here do not add new behavior. They act like labeled front desks that forward callers to the real machinery inside the runtime.

audience.py and subjects.py expose shared labels for who a conversation or disclosed data is meant for. grants.py publishes connection and permission-audit tools. authproxy.py opens the path for adding authentication backends, while operator.py exposes operator-only web session helpers. surface_token.py provides the public entry point for creating and checking permanent surface links. listings.py publishes listing and pagination tools, and objects.py exposes object kinds, types, and helpers. hub.py gathers event and hub classes for live updates. seats.py republishes seat-related tools, and skills.py republishes the building blocks used to define skills. Together, these files form the SDK’s stable outer shell.

## Files in this stage

### Authorization Markers
Stable SDK facades for conversation audiences, permission grants, and disclosed-row subject identifiers.

### `core/src/ufo/sdk/audience.py`

`data_model` · `cross-cutting`

This file is a thin public doorway into the project’s conversation-audience logic. In this system, an “audience” means who a message or turn is meant for, such as everyone in a shared conversation, a specific room, or a foreign room. The real definitions live deeper inside the runtime package, in `ufo.runtime.turns.audience`. This SDK file imports those names and immediately re-exports them under the SDK namespace.

The reason this matters is stability and simplicity. Code using the SDK can write imports from `ufo.sdk.audience` instead of reaching into internal runtime paths. That is like giving visitors a front desk instead of asking them to walk through the building and find the right office. If the internal code is later reorganized, this file can keep the public import path steady.

There is no new behavior here. It does not parse audiences itself, build audience strings itself, or decide who can read what. It simply makes existing pieces available: the `Audience` value type, constants such as the shared-audience marker, and helper functions for creating, parsing, and displaying audience identifiers.


### `core/src/ufo/sdk/grants.py`

`other` · `cross-cutting import-time API surface`

This module is like a clearly labeled service counter at the front of a building. The real work happens elsewhere, in `ufo.runtime.access.grants`, but outside users should not have to know that internal path or depend on it directly. Instead, they can import grant and connection audit objects from `ufo.sdk.grants`.

The file re-exports several public names. These include summary types such as `ConnectionSummary` and `GrantSummary`, event or error types such as `ConnectionRecorded` and `ConnectionPermissionDenied`, and helper functions such as `connection_summaries`, `grant_summaries`, and `main_agent_connections`. In plain terms, these names let users inspect which extension objects connected to what, what grants were involved, and when access was denied.

The comment at the top explains an important project rule: package `__init__.py` files are kept empty, so public SDK features live in named modules like this one. Without this file, users would either lose this convenient public import path or be pushed toward internal modules that may be more likely to change.


### `core/src/ufo/sdk/subjects.py`

`data_model` · `cross-cutting`

In this system, a piece of disclosed data needs an audience: either the whole shared workspace can see it, or a specific member can. This file is the public doorway for those audience labels, called “subjects.” A subject is just a small agreed-upon marker that answers the question, “Who is this row meant for?”

The file does not create new behavior itself. Instead, it re-exports the real definitions from the runtime layer, which is the lower-level part of the system that tracks turns, conversations, and disclosure rules. By doing this, outside SDK code can import names like the shared-workspace subject, the member-subject prefix, and helper functions for building or checking subjects without reaching into runtime internals.

This matters because audience labels need to be spelled exactly the same everywhere. If one part of the project said “shared” one way and another part said it differently, permission checks could fail or data could be shown to the wrong audience. This file acts like a labeled shelf at the front of the store: the goods come from the warehouse, but callers use this stable public shelf instead of walking into the back room.


### Authentication Entrypoints
Public import points for authentication backend integration, operator web sessions, and persistent surface-token helpers.

### `core/src/ufo/sdk/authproxy.py`

`other` · `cross-cutting`

This file exists to make the project’s authentication extension point easier and safer to use. Instead of asking extension authors to know the internal layout of the runtime package, it presents the few names they need in one stable SDK location.

The problem it solves is similar to putting a reception desk at the front of a building. The actual offices are deeper inside, but visitors should not need a map of the whole building. They come to this file and get the official objects for building an auth-proxy extension.

An auth proxy is the piece that turns a source’s account reference into a real credential, meaning the secret or token a connector uses to talk to an outside provider. The file exposes `AuthProxy`, the interface an extension implements; `Credential`, the credential shape connectors receive; `DIRECT_ACCOUNT`, the marker used when a member supplied credentials directly rather than through a brokered account connection; and `AuthProxySpec`, the manifest entry that declares the extension’s auth backend.

There is no runtime logic here. Its importance is in keeping the public API stable. If the internal modules move later, this file can keep the same names available to extension code.


### `core/src/ufo/sdk/operator.py`

`util` · `cross-cutting`

This file is like a labeled front desk for operator web-session features. Other parts of the project, or code using the SDK, can import `FleetDirectory`, `bind_operator_session`, and `resolve_operator_workspace` from here without needing to know where the deeper runtime implementation lives.

The tools it exposes are for operator-only surfaces, such as debug or administration pages. In plain terms, they help decide which workspace an operator is trying to access, check that the request belongs to the right operator-facing domain, bind a shared session cookie after a POST request, and keep track of operator-facing services through a fleet directory.

Why this matters: public import paths are a promise. If callers had to import directly from `ufo.runtime.ext.operator`, the project would be harder to reorganize later without breaking them. By re-exporting through `ufo.sdk.operator`, the SDK can present a clean, stable doorway while the internal layout remains free to change.

There are no functions or classes defined in this file itself. Its job is to make selected runtime objects available under SDK-friendly names.


### `core/src/ufo/sdk/surface_token.py`

`io_transport` · `cross-cutting`

This file is a small public doorway into the project’s token system. A “surface” can mint and verify its own permanent link addresses using surface tokens, without directly carrying around the deploy’s secret token material. That separation matters because secrets should stay in the trusted internal layer, while callers get a safe, simple interface.

The file belongs to `ufo.sdk`, which is described here as a package of thin re-export modules. A re-export is like putting a signpost at a public entrance: the useful tool lives elsewhere, but users are told to come through this named module instead of reaching into internal folders. This gives the project room to reorganize private code later while keeping the public import path steady.

Specifically, it imports `mint_surface_token` and `verify_surface_token` from `ufo.harness.auth.surface_token` and exposes them under the same names. There is no extra logic, no local state, and no functions defined in this file. Its value is in API design: it marks these two token operations as part of the supported public SDK.


### Domain Abstractions
SDK-facing facades for listing, pagination, object kind, object type, and object helper abstractions.

### `core/src/ufo/sdk/listings.py`

`data_model` · `cross-cutting`

Extensions sometimes need to return a list of things, such as portal entries, in smaller chunks instead of all at once. This is called paging: like showing search results one page at a time. This file exposes the public tools for that job through the SDK, which is the safer, intended interface for outside code to use.

The actual logic lives in `ufo.runtime.listings`. Rather than making extension authors import from that internal runtime location, this file re-exports the important names: `ListingCursor`, `ListingPage`, `MalformedCursor`, `page_of`, and `page_query`. Re-exporting means it imports something and immediately makes it available under this module too.

This matters because it creates a clean boundary. Outside code can depend on `ufo.sdk.listings`, while the project remains free to reorganize its internal runtime files later. Without this file, extension authors would either need to know internal package details or risk using paths that may change. There are no functions defined here; it is a public doorway to listing-related types and helpers.


### `core/src/ufo/sdk/objects.py`

`other` · `cross-cutting import-time public API`

This file exists to make the project easier and safer to extend. Instead of asking extension authors to import from many deeper internal paths, it gathers the approved object-related names into one public module: `ufo.sdk.objects`. Think of it like a reception desk in a large building: visitors should go there rather than wandering through private offices.

The file does not create new behavior of its own. It re-exports constants, classes, and helper functions from host, runtime, and schema modules. These include object kind names such as artifacts, conversations, members, workspaces, credentials, surfaces, and agents; object reference and ownership types; list and detail view types; permission-related errors; and small helpers such as `object_page`, `owner_emails`, and `object_agent_id`.

This matters because it creates a stable public contract. Internal modules can move or change over time, but extensions can keep importing from this SDK module. Without this layer, extension code would be more tightly tied to the project’s private layout, making future refactors more likely to break outside users.


### Extension Runtime Surfaces
Stable public imports for hub events, seat tools, and skill building blocks used by extensions.

### `core/src/ufo/sdk/hub.py`

`other` · `cross-cutting import time`

This module is like a clearly labeled front desk for the hub part of the SDK. The real hub code lives deeper inside the project, mostly under `ufo.runtime.hub`, but outside users should not have to know those internal paths. Instead, they can import names such as `Hub`, `InProcessHub`, `LiveFrame`, `Reply`, or `Terminal` from `ufo.sdk.hub`.

The hub is the system piece that represents live communication and activity updates. Its types describe things that can happen during a run, such as text arriving, work being queued, activity changing, costs ticking upward, artifacts changing, or a task reaching a terminal state. This file also re-exports `TextDelta`, which represents a small piece of text output as it streams in.

A project rule says package `__init__.py` files must stay empty, so public SDK imports are placed in named modules like this one. Without this file, extension writers would need to import from internal runtime modules directly, which would make their code more fragile if the project reorganized its internals later.


### `core/src/ufo/sdk/seats.py`

`other` · `cross-cutting import time`

This module is like a clearly labeled front desk for seat-related SDK features. The actual rules and logic live in `ufo.runtime.seats`, but outside code should not have to know that internal location. Instead, an extension can import from `ufo.sdk.seats` and get the public tools it needs.

The file exposes `SeatEntry` and `Seats`, which represent seat state, plus helper functions for common questions such as finding a member by email, checking whether a member is an admin, listing a member’s workspaces, and finding a workspace’s domain. In plain terms, these are the building blocks a seat-reporting job uses when it wants to describe who has access to what.

This matters because it separates the public promise of the SDK from the private layout of the project. If the runtime code is reorganized later, this file can keep the same public imports working. Without this kind of re-export layer, extensions might depend directly on internal paths, making the system more fragile and harder to change safely.


### `core/src/ufo/sdk/skills.py`

`other` · `cross-cutting import time`

This module is like a front desk for skill features. The real work lives elsewhere, in runtime modules that know how to represent skills, read skill definitions, find the root location for skills, and score skill search matches. Rather than asking extension authors or other outside callers to know those internal file paths, this file exposes the important pieces under the friendlier `ufo.sdk.skills` name.

That matters because it keeps the public interface stable. If the internal runtime code is later reorganized, callers can keep importing from this SDK module instead of chasing moving implementation details. It also follows the project rule that package `__init__.py` files should stay empty, so named SDK modules like this one become the public doorway.

The exported items include `RuntimeSkill` and `SkillCard`, which are value objects describing skills; `parse_skill_content`, which turns in-memory skill text into structured skill information; `skill_root`, which points to where skill files belong; `SKILL_LINE_MAX_CHARS`, a limit used when reading or scoring skill text; and `lexical_score`, which gives text matches a simple word-based ranking. In short, this file is small but important: it defines what skill tools the project promises to outsiders.
