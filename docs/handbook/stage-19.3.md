# SDK cross-cutting platform service facades  `stage-19.3`

This stage is shared behind-the-scenes support for extension authors and SDK users. It is made of “facades”: small public doorways that keep import paths stable, even if the deeper internal code moves. Most files do not do the real work themselves. They re-export approved tools so outside code does not depend on private project layout.

Accounting exposes spending report types. Balance exposes prepaid balance, credits, and auto top-up tools. Listings provides listing and paging helpers. Seats publishes seat objects and helpers. Authproxy exposes pieces for credential backends, while credentials publishes selected credential tools. Bearer gives only token-checking helpers, not token creation. Grants exposes connection and audit tools. Operator provides operator-only web session helpers. Surface_token forwards surface token functions. Audience gives shared conversation audience names and helpers. Subjects exposes standard visibility labels, such as workspace-wide or individual-member access. Untrusted gives one common way to mark risky text from tools or extensions. O11y opens logging and metrics tools. Together, these files act like a clean front counter for many platform services.

## Files in this stage

### Commercial service facades
Stable SDK import paths for accounting reports, prepaid balances, listings, and seat-related resource controls.

### `core/src/ufo/sdk/accounting.py`

`data_model` · `cross-cutting`

This file is like a labeled shelf at the front of a store. The real accounting tools live elsewhere, in `ufo.accounting`, but SDK users should not need to know the project’s internal layout to find them. Instead, they can import spending-related objects from `ufo.sdk.accounting`, which is part of the public-facing SDK surface.

The objects exposed here describe workspace spending: reports for agents and members, totals grouped by subject or dimension, usage exports, and a conversion constant for representing dollars in millionths of a dollar. The module also exposes `metered_workspaces`, which points users to the workspaces whose usage can be measured for billing or reporting.

This matters because public import paths are a promise. If outside tools, plugins, or scripts depend directly on internal modules, the project becomes harder to reorganize without breaking them. By keeping this thin re-export layer, the project can move or refactor its internal accounting code later while keeping the SDK-facing import path steady. There are no functions here because the file’s job is not to perform work; its job is to make the right accounting building blocks available in the right public place.


### `core/src/ufo/sdk/balance.py`

`other` · `cross-cutting import-time API exposure`

This is a thin doorway into the system’s balance features. The real balance rules live in `ufo.balance`; this file simply re-exports the approved names from there under the public `ufo.sdk.balance` path. That matters because extensions, such as a billing integration, need a stable and clear place to import balance tools from. Without this file, extension authors would have to reach into internal project modules, which makes their code more fragile if the project is reorganized later.

Think of it like a reception desk. The desk does not perform the accounting work itself, but it tells approved visitors exactly which services are available and sends them to the right place. Here, the exported pieces include balance-related data types such as `Balance`, `Headroom`, and `AutoTopup`, plus actions for reading balances, crediting an account, setting auto top-up preferences, and marking a top-up as verified.

The file also explains an important design choice: `ufo.sdk` uses named modules like this one as its public surface. The package’s `__init__.py` is empty, so callers are expected to import from specific SDK modules rather than from one large catch-all namespace.


### `core/src/ufo/sdk/listings.py`

`io_transport` · `extension development and request handling`

This file is a small public doorway into the project’s listing system. A “listing” here means a paged list of results, such as showing only the next chunk of items instead of returning everything at once. That matters because extensions that answer portal listing requests need a shared way to talk about pages, cursors, and invalid cursor values.

Rather than making extension code import directly from the deeper `ufo.listings` module, this file exposes the important pieces through `ufo.sdk.listings`. That is useful because an SDK, or software development kit, is the part of a project meant to be used by outside code. It gives callers a cleaner and more stable import path, like a front desk that points to the right internal office.

The file re-exports `ListingCursor`, `ListingPage`, `MalformedCursor`, `page_of`, and `page_query` under the same names. `ListingCursor` represents the position used to fetch the next page. `ListingPage` represents one page of results. `MalformedCursor` is the error used when a cursor cannot be understood. `page_of` and `page_query` are helper functions for creating or reading paged results. If this file disappeared, extensions could still possibly reach the lower-level module, but they would lose this intended public SDK surface.


### `core/src/ufo/sdk/seats.py`

`util` · `cross-cutting import-time public API`

This module is like a labeled front desk for seat-related features. The real seat logic lives deeper in `ufo.seats`, but outside code should not have to know that internal location. Instead, extensions can import from `ufo.sdk.seats`, which is meant to be the public, supported path.

The file re-exports the main seat types and helper functions: `SeatEntry`, `Seats`, `member_by_email`, `member_is_admin`, and `member_workspaces`. In plain terms, these are the pieces used to represent who has a seat, inspect members, and ask questions such as “is this member an admin?” or “which workspaces does this member belong to?”

Nothing is calculated here, and no data is stored here. The value of the file is stability and clarity. If the internal project layout changes later, this SDK module can keep the same public imports while pointing to the new internal code. Without this layer, extension code might import directly from internal modules and become fragile when the project is reorganized.


### Identity and access facades
Public re-export surfaces for credential backends, bearer-token checks, grants, operator sessions, and surface tokens.

### `core/src/ufo/sdk/authproxy.py`

`other` · `extension setup and credential resolution`

This file is a small public doorway into the project’s authentication-proxy system. An authentication proxy is the part that turns a source’s account choice into a usable `Credential`, meaning the secret or token a connector needs to talk to an outside provider. Without this doorway, extension code would have to import directly from internal connector modules, which would make extensions more fragile if the project later rearranged its internals.

The file does not create new behavior. Instead, it re-exports four important names. `AuthProxy` is the interface an extension implements when it wants to provide credentials. `Credential` is the credential object the connector ultimately receives. `DIRECT_ACCOUNT` is the marker used when a source should use a member-provided credential directly, instead of going through a brokered account connection. `AuthProxySpec` is the manifest entry that lets an extension advertise that it provides an auth-proxy backend.

In everyday terms, this file is like a reception desk: it does not do the credential lookup itself, but it tells extension writers exactly which forms and labels to use. If there is only one backend, it can be chosen automatically. If there are several, configuration selects the backend. That chosen backend then resolves credentials for sources that use direct account credentials.


### `core/src/ufo/sdk/bearer.py`

`util` · `request handling`

A bearer token is like a temporary badge: a gateway gives it to a user, and an extension can check the badge before trusting the request. This file is a small public doorway for that checking step. It re-exports selected names from `ufo.bearer`, including the login path, the session cookie name, and helper functions that verify a token and read trusted claims from it.

The important safety choice is that extensions do not receive or store the signing secret. The underlying verification functions look up `UFO_TOKEN_SECRET` themselves when they need it. That means an extension can ask, “Is this token genuine, and what workspace does it belong to?” without ever holding the key that could mint or forge tokens.

Without this file, extension authors might import from deeper internal modules or duplicate token logic, both of which would make the system harder to keep safe and consistent. This file acts like a clearly labeled service window: extensions can check badges here, but the badge-printing machine stays in the core control plane.


### `core/src/ufo/sdk/credentials.py`

`other` · `cross-cutting`

This file does not create new credential behavior itself. Instead, it acts like a front desk for the credentials part of the SDK: it points callers to the approved credential classes, error types, and helper functions that live deeper inside the project. That matters because extensions should not need to know the project’s internal folder layout, and they should not accidentally depend on private details that may change later.

The imported names include credential-related error types, a credential store type, and helper functions for opening an installation, naming credential objects, and checking which workspace is authorized for a slot. By re-exporting them here, the project gives extension authors one clear, supported place to import from: `ufo.sdk.credentials`.

If this file were removed, extensions might have to import directly from `ufo.credentials`. That would make the public API less clear and could make future internal refactors break extension code. In everyday terms, this file is a labeled service window: the work happens in the back office, but outsiders are told to come here.


### `core/src/ufo/sdk/grants.py`

`other` · `import time / SDK use`

This file is a small doorway into the project's grant and connection-audit features. The project keeps `ufo.sdk` as the public area for extension code, but its package initializer is intentionally empty. That means public SDK items need to live in named modules like this one.

Here, the module imports selected classes and helper functions from `ufo.grants` and exposes them under `ufo.sdk.grants`. In everyday terms, it is like a front desk: the real work happens in the office behind it, but outsiders are told to come to this desk because its location is stable and meant for public use.

The exported items cover things like summaries of granted permissions, records of connections, denied-connection errors, and helper functions for listing or naming those records. Without this file, extension objects would need to import directly from the deeper internal module. That would make the public API more fragile, because internal module paths are easier to change than SDK-facing paths.

There are no functions or classes defined here. Its value is in clearly marking which grant-related objects are safe and intended for outside code to use.


### `core/src/ufo/sdk/operator.py`

`orchestration` · `request handling`

This file is a small doorway. Operator-only pages and debug tools need two common pieces: a way to figure out which workspace an operator is trying to use, and a way to attach the operator session to the shared session cookie after a POST request. Instead of making callers know the deeper internal path where those helpers live, this file exposes them from the public SDK area.

That matters because imports are like addresses. If every tool imported directly from the internal extension module, changing that internal location later would break many places. By re-exporting the helpers here, the project gives callers one predictable address: `ufo.sdk.operator`.

The two exported names come from `ufo.ext.operator`. `resolve_operator_workspace` is the resolver that checks the operator-facing domain and reads the `?ws=` query parameter to decide which workspace is being targeted. `bind_operator_session` is the POST-side helper that connects the operator’s authenticated session to the shared cookie. In plain terms, one helper answers “which workspace is this operator trying to reach?”, and the other says “remember this operator session in the browser.”


### `core/src/ufo/sdk/surface_token.py`

`other` · `cross-cutting`

This file is like a clearly labeled service window for two token-related tools: one that creates a surface token and one that checks a surface token. A surface token is a permanent link-style address that a “surface” can mint and verify without directly holding the deployment’s secret token key. That separation matters because it lets public SDK users do the right thing without reaching into private project internals.

The project keeps `ufo.sdk` as a package of small named modules, rather than putting code in `__init__.py`. This file exists so users can write imports against `ufo.sdk.surface_token` and get the public token helpers from there. Internally, it simply re-exports `mint_surface_token` and `verify_surface_token` from `ufo.surface_token` under the same names.

Nothing is calculated here, and no secrets are read here. If this file were removed, the underlying token code might still exist, but SDK users would lose this clean public import path. That would make the API harder to understand and more fragile, because callers might start depending on internal module locations instead of the intended SDK boundary.


### Audience and disclosure markers
Shared SDK names for conversation audiences, data visibility subjects, and untrusted-content boundaries.

### `core/src/ufo/sdk/audience.py`

`data_model` · `cross-cutting`

This file does not define new behavior itself. Instead, it re-exports audience-related pieces from `ufo.audience` under the public SDK path `ufo.sdk.audience`. In plain terms, it is like a reception desk: the real work happens elsewhere, but this desk gives outsiders a clear, official place to ask for it.

The audience helpers describe who a message or conversation is meant for. That can include shared audiences, room-specific audiences, foreign-room audiences, and functions for turning audience strings into structured values or readable labels. By collecting those imports here, the SDK can promise users a stable import path even if the internal source files are reorganized later.

Without this file, SDK users might need to import directly from `ufo.audience`, which would make the internal module layout part of the public contract. That is brittle: moving or renaming internal files could break outside applications. This small wrapper protects users from that kind of breakage and makes the SDK easier to discover.


### `core/src/ufo/sdk/subjects.py`

`data_model` · `cross-cutting`

This file is a small public doorway into the project’s subject system. A “subject” here means an audience label: it says whether some data is visible to the whole shared workspace or to a specific member. Other parts of the system can then ask, “Is this row disclosed to everyone, or only to this person?”

The file does not create new rules itself. Instead, it re-exports a few trusted pieces from the lower-level `ufo.subjects` module: the shared subject value, the prefix used for member-specific subjects, and helper functions for building or recognizing those subjects. This is like putting the most commonly used tools from a toolbox on the front counter so SDK users do not need to know where they are stored internally.

This matters because disclosure subjects are used consistently across rows and conversation audiences. If every caller invented its own wording for “shared” or “member,” access decisions could become inconsistent. By offering these atoms through the SDK, the project keeps audience labels stable and easy to import.


### `core/src/ufo/sdk/untrusted.py`

`util` · `cross-cutting`

This is a very small bridge file. Its job is to expose the core project’s existing untrusted-content marker through the SDK path. “Untrusted” here means text that came from outside the system’s control, such as a probe’s standard output, a provider response, or third-party extension output. That kind of text may contain misleading instructions or unsafe content, so the system needs a consistent way to fence it off.

The file imports `wall` from `ufo.untrusted` and makes it available as `ufo.sdk.untrusted.wall`. In plain terms, it is like putting the same warning label dispenser at the SDK counter that the core system already uses internally. Without this file, extension or SDK code might invent its own marker, use the wrong one, or fail to mark outside content at all. That would make it harder for renderers and safety-sensitive paths to recognize where untrusted text begins and ends.

There are no functions or classes here. The important behavior is the shared alias: SDK callers and core code can speak the same language when labeling untrusted wall content.


### Observability facade
A stable public doorway for extensions to report logs and metrics through the platform observability tools.

### `core/src/ufo/sdk/o11y.py`

`util` · `cross-cutting`

This file is a small public wrapper around the project’s observability tools. “Observability” means the clues a running system leaves behind, such as logs and metrics, so people can understand what it is doing and diagnose problems.

Extensions need a way to write structured logs, send warnings, record metrics, and mark turn-level profiling information. But the project does not want each extension to invent its own metric names. If everyone could make up names freely, dashboards and alerts would become messy and unreliable, like a warehouse where every box uses a different labeling system.

So this file re-exports a controlled set of functions from the core `ufo.o11y` module: `log`, `warn`, `emit_metric`, and `turn_profile`. Extension code can import them from the stable SDK path, while the actual registry and enforcement stay inside core. That means an extension can emit only metrics the core system already knows about; if it tries something undeclared, it fails loudly instead of quietly creating a confusing new metric.

There are no new functions or classes here. Its importance is in the boundary it creates: extensions get a simple, supported API, and the core system keeps one authoritative place for observability behavior.
