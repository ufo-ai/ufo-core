# Public SDK auth, credentials, accounting, and operator utilities  `stage-18.1`

This stage is shared behind-the-scenes support for extension authors and administrators. It provides stable “front doors” in the public SDK, so outside code can use approved security, accounting, and operator tools without reaching into private core modules that may change.

The accounting file exposes the project’s existing spending and accounting summary types. Grants does the same for grant audit summaries and helper access, giving a safe way to inspect who was allowed to do what. Seats exposes the core seat-related types and helpers, so code can read seat or usage information through a predictable SDK path.

Bearer provides token verification helpers. A bearer token is a secret string used to prove a request is allowed; this file only exposes checking, not creating, tokens. Credentials exposes the credential tools extensions are permitted to use. Operator gathers helpers for protected operator-only web sessions, such as debug or admin tools. Finally, o11y exposes structured logging helpers, so extensions can write consistent logs and warnings.

## Files in this stage

### Accounting and entitlement summaries
Public SDK facades for externally consumable accounting, grant audit, and seat summary types and helpers.

### `core/src/ufo/sdk/accounting.py`

`data_model` · `cross-cutting`

This module is like a labeled front desk for accounting-related SDK features. The real definitions live in `ufo.accounting`, but users of the public `ufo.sdk` package are meant to import them from named SDK modules such as this one. That keeps the public interface tidy and gives the project room to move internal code later without forcing users to change their imports.

The accounting objects exposed here describe workspace spending and usage totals. For example, they include report-shaped objects such as `SpendReport`, totals grouped by subject or dimension, and a constant for converting dollars into micro-dollars. A micro-dollar is one millionth of a US dollar, which lets the system store money-like values as whole numbers instead of fragile decimal fractions.

Nothing in this file performs input, output, billing math, or database work. Its job is to re-export selected names exactly as part of the SDK surface. Without this file, code that expects to use `ufo.sdk.accounting` would either fail to import or would need to reach into internal modules directly, making the boundary between public API and internal implementation less clear.


### `core/src/ufo/sdk/grants.py`

`other` · `import time / SDK use`

This file exists to keep the public SDK interface tidy and stable. Instead of asking users to know where grant-related code lives inside the project, it re-exports two names from `ufo.grants`: `GrantSummary` and `grant_summaries`. A re-export means the file does not create new behavior itself; it imports something from one place and makes it available from another, more public place.

The reason this matters is boundary control. The SDK is the part outsiders are meant to use, while the deeper `ufo` modules may contain internal details that can change more freely. This file is like a reception desk: it does not do the work itself, but it directs users to the approved service window. If it were missing, code that wants the official SDK path for grant summaries would either fail to import these names or would have to reach into less stable internal paths.

The module also follows a project rule noted in the comment: `ufo.sdk` keeps its package initializer empty, so public SDK names live in small named modules like this one.


### `core/src/ufo/sdk/seats.py`

`other` · `cross-cutting; active when SDK users import seat-related public API names`

This module is like a front desk for the seat-reporting part of the system. The real seat logic lives deeper in `ufo.seats`, where the project defines things like seat records, seat snapshots, and errors for invalid seat changes. Instead of asking extension authors or SDK users to know that internal location, this file re-exports the useful pieces from one public module.

That matters because public import paths are part of the system’s promise to users. If an extension imports `Seats` or `SeatSnapshot` from `ufo.sdk.seats`, the project can later reorganize its internal files without forcing every extension to change, as long as this public re-export stays in place.

The names exposed here cover both data and rules: seat entries and snapshots describe the current state; exceptions such as `SeatLimitReached`, `UnknownMember`, and `OwnerSeatRevocation` explain why a requested write is not allowed; helpers such as `member_workspaces` and `owner_conversation` support building seat-reporting candidates. There are no functions defined in this file, and nothing runs beyond normal Python import behavior. Its job is clarity and stability at the boundary between core code and SDK users.


### Authentication and credential access
Stable public imports for token verification, credential utilities, and operator-only session helpers.

### `core/src/ufo/sdk/bearer.py`

`util` · `cross-cutting token verification during request handling`

This file is a small public doorway into the project’s bearer-token verification code. A bearer token is like a temporary pass: whoever presents it can prove they were authorized by the gateway. Surface extensions need to check these passes, but they should not be able to mint new ones or see the secret key used to protect them.

To keep that boundary clear, this file simply re-exports three verification helpers from `ufo.bearer`: `verify_token`, `verified_claims`, and `workspace_claim`. “Re-export” means it imports names from another module and makes them available here, so outside code can consistently import them through the SDK path.

The important safety idea is in the module comment: extensions pass in a token, and the underlying verification code looks up `UFO_TOKEN_SECRET` itself. That means extension code never receives or stores the signing secret. Without this file, extensions would either need to import from a less stable internal location, or the project might blur the line between code that verifies tokens and code that creates them. This file acts like a one-way inspection window: extensions can check whether a pass is valid, but they are not handed the stamp that creates passes.


### `core/src/ufo/sdk/credentials.py`

`other` · `cross-cutting`

This file does not create new credential behavior itself. Instead, it re-exports a small, approved set of objects and functions from the internal `ufo.credentials` module. A re-export means “make something available from here, even though it is defined somewhere else.”

The reason this matters is boundary-setting. Extensions need to request or use credentials, but they should not depend on every internal detail of how credentials are stored or minted. This file acts like a customer service counter: it exposes only the official forms and services an extension may touch, while the back room stays private.

The exported names include error types for credential failures, a `CredentialStore` for working with stored credentials, and helper functions such as `authorized_slot_workspace` and `open_installation`. By importing them through `ufo.sdk.credentials`, extension authors get a stable public API. If the internal layout changes later, this SDK file can keep the public import path steady.

Without this file, extension code would either need to import from internal modules directly, which makes it fragile, or duplicate knowledge about where credential pieces live.


### `core/src/ufo/sdk/operator.py`

`other` · `import time and request handling`

This file is like a labeled front desk for operator authentication tools. The real work lives in `ufo.ext.operator`, but this module makes those tools available from the public SDK path `ufo.sdk.operator`. That matters because other code can depend on this stable import location without knowing where the implementation happens to live internally.

The helpers it exposes are all about operator-only access. They include the shared operator session cookie name, a way to bind a browser session after a POST request, a bearer-token checker, and a resolver that works out which workspace an operator wants, including reading a `?ws=` query value. In plain terms, these pieces help make sure that debug or operator surfaces are only used by the right people and are tied to the right workspace.

There is no custom logic here. Each imported name is immediately exported again under the same name. If this file disappeared, tools importing from `ufo.sdk.operator` would break even though the lower-level implementation might still exist.


### Structured logging utilities
Public SDK access to UFO's existing structured logging and warning helpers.

### `core/src/ufo/sdk/o11y.py`

`util` · `cross-cutting`

This file is a small bridge between the internal logging system and outside extension code. “Structured logging” means recording events in a consistent shape, so tools and people can search, filter, and understand them more easily than plain text messages. Without this file, extension authors would need to import logging helpers from UFO’s internal modules, which makes their code more fragile if the internal layout changes.

The file does not create new logging behavior. Instead, it re-exports two existing helpers: `log`, for normal structured log messages, and `warn`, for warning messages. Think of it like a front desk in a building: visitors do not need to know which back-office room contains the equipment; they just go to the public desk. Here, `ufo.sdk.o11y` is that public desk.

This matters because SDK files are a promise to extension developers. They define the supported surface area of the system. By importing from `ufo.sdk.o11y`, extensions can use logging in the intended way while the core project remains free to reorganize its private internals later.
