# SDK access, identity, billing, and visibility facades  `stage-19.4`

This stage is a set of public front doors for extension authors and other SDK users. It is shared behind-the-scenes support, not the main work loop. Its job is to let outside code use approved identity, access, billing, and visibility tools without depending on private internal paths that may change.

The files mostly re-export trusted pieces from deeper runtime code. accounting exposes the objects used to describe workspace cost reports, while balance exposes tools for prepaid credit, payments, and auto top-ups. authority publishes the allowed execution authority names, and authproxy publishes authentication-proxy types. bearer provides only safe bearer-token checking, meaning it can verify login tokens but not create secret ones. credentials exposes credential objects, and grants exposes grant and connection audit tools. operator gives operator-only web pages the same session helpers and access rules as the runtime. seats exposes seat state and rules for membership or licensing. subjects names who can see disclosed data, such as a whole shared workspace or a specific member. surface_token exposes helpers for tokens used by surface-facing authentication. Together, these files act like a reception desk: callers get the right approved tools without entering the engine room.

## Files in this stage

### Billing and spend facades
Public SDK entry points for cost-reporting objects and prepaid billing balance operations.

### `core/src/ufo/sdk/accounting.py`

`data_model` · `cross-cutting`

This file is a small public doorway into UFO’s accounting data. Instead of asking users to know the internal package layout, it lets them import spending report types from `ufo.sdk.accounting`. That matters because internal code can move around over time, while the SDK import path can stay friendly and stable.

The objects exposed here are used for reporting workspace spend: totals by dimension, member-level spending, full spend reports, usage exports, and a helper for finding metered workspaces. A “metered” workspace is one whose usage can be measured and turned into cost. The file also exposes `MICRO_USD_PER_USD`, a conversion constant for money stored in micro-dollars, meaning one-millionth of a US dollar. This avoids floating-point rounding problems when adding costs.

Think of this module like a labeled shelf at the front of a warehouse. The items still live deeper inside the system, but users do not need to wander the aisles to find them. They can use this shelf as the supported public surface for accounting-related SDK imports.


### `core/src/ufo/sdk/balance.py`

`other` · `cross-cutting`

This is a thin doorway into the system’s billing balance code. The real rules and storage logic live in `ufo.runtime.billing.balance`; this file simply re-exports the important names from there under the public `ufo.sdk.balance` module.

That matters because extensions should not need to know the project’s internal folder layout. A billing extension can import from this stable SDK path to read a workspace’s prepaid balance, record a settled payment as credit, inspect recent purchases, or configure automatic top-ups. If the internal runtime code moves later, this SDK file can keep the public import path the same.

An everyday analogy: this file is like a reception desk. It does not perform the accounting itself, but it tells callers where to get the official forms and services. The accounting rules remain in the core billing module, while outside code gets a clean, named place to access them.

There are no functions defined here. Every exported item is imported from the runtime billing balance module and made available unchanged.


### Authority and access facades
Stable public imports for execution authority, authentication proxy, bearer verification, credentials, and grants.

### `core/src/ufo/sdk/authority.py`

`other` · `cross-cutting`

This file is a small public-facing wrapper. It does not create new behavior itself. Instead, it re-exports selected authority objects, types, and helper functions from `ufo.runtime.authority` so extension authors can import them from the SDK path.

In plain terms, an execution authority describes whose permission or workspace context some extension-owned work should run under. For example, work may belong to a whole workspace or to a specific member. Without a public SDK file like this, extension code would need to reach into internal runtime modules directly, which makes the project harder to change safely. This file is like a reception desk: it points outsiders to the right approved tools without exposing the whole back office.

The exported names include authority types such as `ExecutionAuthority`, `WorkspaceAuthority`, and `MemberAuthority`; a ready-made `WORKSPACE_AUTHORITY`; an `AuthorityUnavailable` error; and helpers that convert between member identifiers and authority objects. Keeping these imports centralized makes the SDK contract clear: these are the authority concepts extension code may rely on.


### `core/src/ufo/sdk/authproxy.py`

`other` · `cross-cutting`

This file exists to make the project’s extension API easier and safer to use. An authentication proxy is the part of an extension that can turn a saved account choice or provider setting into a usable credential, such as a token or key, for a connector that talks to an outside service.

Instead of asking extension authors to import these pieces from deeper internal modules, this file re-exports them from a stable SDK path. That means outside code can depend on `ufo.sdk.authproxy` without needing to know where the runtime keeps its connector and manifest definitions internally. It is like a front desk: visitors ask here for the forms they need, even though the forms are stored in back-office cabinets.

The exported pieces describe the contract between an extension and the feed-sync system: `AuthProxySpec` declares an auth backend in an extension manifest, `AuthProxy` is the interface the backend implements, `Credential` is what gets produced for a connector to authenticate with, and `DIRECT_ACCOUNT` marks sources that should resolve credentials directly through the selected auth backend rather than through a broker connection.

There is no active logic here. Nothing is calculated or stored. Its importance is in keeping the public API clean and insulating extension code from internal module layout changes.


### `core/src/ufo/sdk/bearer.py`

`other` · `request handling and cross-cutting authentication`

This file is a small public doorway into the project’s bearer-token authentication code. A bearer token is like a temporary wristband: if a request carries a valid one, the system can trust that it came from someone who was already allowed in. The actual implementation lives in `ufo.harness.auth.bearer`, but this SDK file exposes the parts that outside surface extensions are meant to use.

The important safety choice is that extensions can verify tokens without ever receiving the signing secret, which is the private value used to create trustworthy tokens. Each verification function looks up `UFO_TOKEN_SECRET` for itself inside the core authentication code. That means an extension can ask, “Is this token valid, and what workspace does it belong to?” without being handed the key that could mint new tokens.

The file re-exports route and cookie names such as login, logout, and session cookie constants, plus helpers for checking a token and reading trusted claims from it. Without this file, extension authors would either have to import from an internal module directly or duplicate authentication knowledge, both of which would make the system more fragile and less safe.


### `core/src/ufo/sdk/credentials.py`

`other` · `cross-cutting import surface`

This file does not create new credential behavior itself. Instead, it re-exports selected credential tools from the deeper runtime package, like putting approved items from a locked storeroom onto a public counter. The real implementations live in `ufo.runtime.access.credentials`, but this file decides which of those names are part of the public SDK surface.

That matters because extensions should not depend directly on internal runtime paths. Internal code can be reorganized later, but the SDK path can stay stable. Without this file, extension authors would either need to know the project’s internal layout or import private implementation details, which would make their code more fragile.

The exported names cover credential request errors, credential value errors, the credential store interface, and helper functions for naming credential objects and reading deployment environment information. In plain terms, these are the pieces an extension may need when asking for secrets or credentials safely, checking whether those requests are valid, and referring to credential-related objects in the expected way.


### `core/src/ufo/sdk/grants.py`

`other` · `cross-cutting import-time public API surface`

This module is like a labeled service window at the front of a building. The real work happens deeper inside the project, in `ufo.runtime.access.grants`, but outside users should not have to know that internal layout. Instead, they can import connection and grant audit views from `ufo.sdk.grants`.

The file exposes names such as `ConnectionSummary`, `GrantSummary`, `ConnectionRecorded`, and `ConnectionPermissionDenied`. These are used to inspect or report which extension objects were allowed to connect to which accounts or connectors, and when a connection was refused. It also exposes helper functions such as `connection_summaries`, `grant_summaries`, and `main_agent_connections`, which provide readable audit information.

The comment at the top explains an important project rule: `ufo.sdk` keeps its package initializer empty, so public SDK items live in named modules like this one. Without this file, extension code would need to import directly from internal runtime paths. That would make extensions more fragile, because an internal file move could break their imports even if the public feature still exists.


### Sessions, seats, and visibility facades
SDK-facing contracts for operator sessions, seat state, disclosed-row subjects, and surface token helpers.

### `core/src/ufo/sdk/operator.py`

`util` · `cross-cutting; imported by operator/debug surfaces during startup and request handling`

This file is a small public doorway into the operator web-session system. An “operator” here means a trusted human or tool using special internal/debug web pages, not an ordinary user-facing surface. Those pages need a consistent way to decide which workspace a request is for, confirm that the request belongs to the operator domain, attach the shared operator session cookie, and keep a directory of fleet/workspace entries they can index by.

Rather than making every caller import from the deeper runtime package, this file re-exports three names from `ufo.runtime.ext.operator`: `FleetDirectory`, `bind_operator_session`, and `resolve_operator_workspace`. This is like putting the most-used switches for a machine on a clean front panel, even though the wiring lives behind the wall.

The important behavior is that this module is only a facade. If the real operator session logic changes, it changes in the runtime module, while code using the SDK can keep importing from this stable location. Without this file, operator-only tools would either depend directly on internal paths or each choose their own imports, making the public API more fragile and harder to keep consistent.


### `core/src/ufo/sdk/seats.py`

`other` · `cross-cutting`

This module does not create new behavior of its own. Instead, it re-exports selected names from `ufo.runtime.seats`, which is where the real seat logic lives. A “seat” here means a recorded place or membership slot for a person in a workspace-like system. The exported pieces include the seat data shapes, such as `SeatEntry` and `Seats`, plus helper functions for common questions like finding a member by email, checking whether a member is an admin, listing a member’s workspaces, and reading a workspace domain.

The reason this file exists is to give outside extension code a clean, intentional import path: `ufo.sdk.seats`. That matters because it separates the public SDK from the project’s internal layout. If the runtime package changes later, the project can keep this small wrapper stable so extensions do not have to be rewritten. Think of it like a reception desk: visitors ask for what they need at the front desk, while the office can reorganize rooms behind the scenes.

Without this file, extension authors would need to import directly from runtime code, which would blur the line between supported public API and internal implementation details.


### `core/src/ufo/sdk/subjects.py`

`data_model` · `cross-cutting`

This file is a public doorway into a lower-level part of the system. In this project, a “subject” is a label that represents an audience: for example, everyone in a shared workspace, or one specific member. These labels are used when deciding who may see a row of data, and the same idea is also used to describe a conversation audience.

Rather than making outside code import directly from the runtime internals, this file re-exports the important subject pieces through the SDK. That matters because SDK users can rely on this file as the stable, friendly import path, while the internal implementation can stay tucked away.

The file does not create new behavior. It simply brings four names from `ufo.runtime.turns.subjects` into the SDK namespace: a prefix used for member subjects, the shared subject value, a helper that builds a member subject, and a helper that checks whether a subject is the shared one. Think of it like a labeled shelf at the front desk: the actual tools live in the workshop, but users know they can always pick them up here.


### `core/src/ufo/sdk/surface_token.py`

`other` · `cross-cutting`

This module is like a signpost at the public entrance of the project. A “surface token” is used for permanent link addresses that a surface can mint and later verify, without the surface needing to hold the deploy-wide secret directly. That matters because it keeps the public SDK tidy and safer: outside code can import the token tools from `ufo.sdk.surface_token` without needing to know where the deeper authentication code lives.

The file re-exports two functions from `ufo.harness.auth.surface_token`: one for minting a surface token and one for verifying it. “Re-export” means the function is defined somewhere else, but this file makes it available under a friendlier public path. This is useful for keeping the project’s internal layout flexible. The maintainers can move or refactor the real implementation later while preserving the public import path that users rely on.

There is no extra behavior here, no setup, and no hidden state. When Python imports this module, it imports the two real functions and exposes them under the same names. Without this file, SDK users would have to reach into internal harness modules, which would make their code more tightly coupled to project internals.
