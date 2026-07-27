# SDK identity, authorization, credentials, and administration doorways  `stage-20.2`

This stage is shared behind-the-scenes support for people who build extensions or outside tools on top of the system. It is not where authentication or accounting is invented. Instead, these files act like labeled front doors in the SDK, the public toolkit that other code is meant to import from. They hide the deeper internal layout so that outside code can stay stable even if the project is reorganized.

The accounting doorway exposes spend reports and usage totals. Authproxy gathers the pieces needed to plug in an authentication backend. Bearer provides safe access to bearer-token checks, where a bearer token is a digital pass presented with a request. Credentials exposes approved tools for working with stored access information. Grants opens access to grant audit summaries, showing who received what permissions. Hub republishes hub-related types used for connecting to central services. Operator exposes helpers for operator-only web sessions. Seats provides the public rules and data types for seat policy. Together, these modules form a controlled reception desk for identity, permission, and administration features.

## Files in this stage

### Accounting doorway
Public SDK re-exports for accounting reports, spend data, and usage totals.

### `core/src/ufo/sdk/accounting.py`

`other` · `cross-cutting SDK import`

This file exists to keep the public software development kit, or SDK, simple and stable. An SDK is the part of a project that other programs are expected to import and use. Instead of asking users to dig into the internal `ufo.accounting` module, this file re-exports the accounting pieces that are meant to be part of the public interface.

The objects exposed here describe money and usage information: totals by subject, totals by dimension, price digest totals, full spend reports, and usage exports. It also exposes `MICRO_USD_PER_USD`, a constant used for representing US dollar values in millionths of a dollar, and `metered_workspaces`, a helper from the accounting layer.

There is no new business logic here. Think of it like a labeled shelf in a library: the books are stored elsewhere, but this shelf tells visitors exactly where the approved accounting materials are. Without this file, callers would either need to import from deeper internal modules or the project would lack a clean public place for accounting-related SDK imports.


### Authentication and credentials
Stable SDK entry points for authentication backend integration, bearer-token verification, and credential access.

### `core/src/ufo/sdk/authproxy.py`

`data_model` · `extension development and authentication setup`

This file is a small public doorway into the project’s authentication-proxy system. An authentication proxy is the part an extension can provide when the system needs credentials, such as a token or password-like secret, to connect to an outside feed source.

The important reason this file exists is stability. The real classes and constants live in internal modules such as `ufo.connectors` and `ufo.ext.manifest`, but extension authors should not have to know or depend on that internal layout. By importing from `ufo.sdk.authproxy`, they get the official public interface instead. If the project later moves the internal code around, this file can keep the same public names and prevent extensions from breaking.

It re-exports four names. `AuthProxySpec` describes, in an extension manifest, that an extension offers an authentication backend. `AuthProxy` is the interface the extension implements to turn an account reference into an actual `Credential`. `Credential` is the credential object used by connectors when talking to a provider. `DIRECT_ACCOUNT` marks sources where the member supplied credentials directly, instead of going through a broker or shared grant.

An everyday analogy: this file is like a labeled service counter. The supplies are stored in a back room, but visitors only need to know where the counter is.


### `core/src/ufo/sdk/bearer.py`

`io_transport` · `request handling`

A bearer token is like a temporary pass: whoever presents it can prove that a trusted part of the system allowed them to act. Surface extensions need to check these passes, but they should not be given the signing secret itself. This file exists to make that boundary clear.

Instead of implementing token logic here, it publicly re-exports three verification helpers from `ufo.bearer`: one to verify a token, one to read verified claims, and one to extract the workspace claim. A “claim” is a piece of information inside the token, such as which workspace it belongs to. The important design choice is that callers pass in the token, and the real verification code looks up `UFO_TOKEN_SECRET` internally. That means an extension can ask, “Is this pass valid, and what does it say?” without ever holding the key that creates valid passes.

Without this file, extensions might import deeper internal modules directly, or worse, start depending on signing details they should not know. This small re-export keeps the public SDK tidy and reinforces the security rule: the control plane mints tokens; extensions only verify them.


### `core/src/ufo/sdk/credentials.py`

`other` · `cross-cutting`

This file exists to keep the public software development kit, or SDK, tidy and stable. An SDK is the part of a project that outside extension authors are expected to use. Instead of asking those authors to reach into the internal `ufo.credentials` module, this file re-exports only the credential items that are meant to be public.

In plain terms, it is like a front desk. The real work happens in another room, but the front desk tells visitors which services are available and gives them safe access to those services. Here, those services include the credential store, ways to open an installation, a helper for authorized workspace access, and errors that explain credential-related failures.

There is no new logic here. Nothing is calculated or changed. The file simply imports selected names from `ufo.credentials` and publishes them under the SDK path. This matters because it gives extension code a stable import location. If the internal project layout changes later, extension authors can keep using `ufo.sdk.credentials` as long as this file continues to point to the right internal objects.


### Grants and hub access
Public doorways for grant-audit summaries and hub-related SDK types.

### `core/src/ufo/sdk/grants.py`

`other` · `cross-cutting`

This module is part of the public SDK, which means it is meant for code outside the core project to import and use. Its job is deliberately small: it re-exports two grant-related names from the internal `ufo.grants` module. A “re-export” means it imports something from one place and makes it available from another, more stable place.

The reason this matters is that outside users should not have to know where the project keeps its internal grant logic. Internal file locations can change as the project grows. By importing through `ufo.sdk.grants`, users get a cleaner and more dependable path. It is like a reception desk: visitors ask at the desk instead of wandering through private offices.

The two public items exposed here are `GrantSummary`, which represents a summarized view of grant information, and `grant_summaries`, which provides those summaries. The file does not add new behavior, transform data, or run any setup. It simply defines what part of the internal grant-audit feature is safe and intended to be read by connector or SDK users.


### `core/src/ufo/sdk/hub.py`

`io_transport` · `cross-cutting`

This file is like a clearly labeled front desk for the hub part of the SDK. The real implementations live elsewhere, mainly in `ufo.hub` and `ufo.models.interface`, but users should not have to know those internal paths. Instead, they can import public names such as `Hub`, `InProcessHub`, `LiveFrame`, `ToolCall`, and `TextDelta` from `ufo.sdk.hub`.

The project keeps package `__init__.py` files empty, so public SDK entry points are placed in named modules like this one. That avoids hidden code running when a package is imported, while still giving users a clean import path.

The names exported here describe the pieces used around a “hub”: a coordination point that works with live frames of activity, tool calls, terminal output, cost updates, parked work, and skill loading. `TextDelta` is also re-exported because hub users may need to work with incremental text updates.

If this file were missing, users could still reach the underlying classes by importing from internal locations, but that would make the SDK harder to use and more fragile. This file protects users from internal layout changes by acting as a stable public doorway.


### Operator sessions and seats
SDK re-exports for operator-only web-session helpers and seat-related policy or data types.

### `core/src/ufo/sdk/operator.py`

`other` · `cross-cutting`

This file is a small public doorway into the operator authentication tools. The real work lives in `ufo.ext.operator`, but this module lets other parts of the project import those tools through `ufo.sdk.operator`, which is a cleaner and more stable path.

The helpers it exposes are all about protecting an operator-only web surface: a special area meant for trusted operators, not regular users. One exported value names the shared operator session cookie. The others help identify an operator request, resolve which workspace is being targeted through a `?ws=` query value, accept a bearer token (a token sent with a request to prove access), and bind the shared session cookie after a POST request.

The main value of this file is consistency. Without it, every debug tool or operator page might reach directly into the internal `ufo.ext.operator` module. That would make future refactoring harder, because many callers would depend on the internal location. This file acts like a reception desk: callers ask here for the operator-session tools, and this module points them to the real implementation behind the scenes.


### `core/src/ufo/sdk/seats.py`

`other` · `cross-cutting import time`

This file is like a front desk for seat-related features. In this project, a “seat” appears to mean a member’s allowed place or access slot in a workspace-like system. The real logic lives in `ufo.seats`, where the project defines things such as seat records, snapshots of seat state, limits, and errors for invalid changes. This SDK file re-exports those names so outside code can import them from `ufo.sdk.seats` instead of reaching into the internal core module directly.

That matters because public imports are a promise. If extensions or integrations depend on `ufo.sdk.seats`, the project can reorganize its internal files later without breaking those users, as long as this public doorway keeps offering the same names. Without this file, outside code would either need to know the internal layout of the project or duplicate seat rules, both of which would make changes riskier.

The module includes seat state objects, validation-related errors, and helper functions for finding member workspaces or the owner conversation. It keeps the rules in core, while letting extensions decide when to apply them.
