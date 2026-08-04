# SDK Identity, Auth, Governance, and Accounting Facades  `stage-22.4`

This stage is shared behind-the-scenes support for people building on top of the system. It does not run the main work itself. Instead, it provides stable “front doors” in the public SDK, so outside extensions can import the right tools without depending on the project’s private folder layout.

Each file is a small facade, like a labeled counter in a service desk. accounting.py exposes accounting and spending-related objects, but does not calculate costs itself. audience.py exposes names and helpers for deciding who a conversation is meant for. authproxy.py gathers the records and types used when an external connector works through an authentication proxy. bearer.py exposes safe checks for bearer tokens, which are proof strings sent with requests, without revealing the secret used to make them. credentials.py republishes approved credential classes and helpers. grants.py exposes permission grants and connection audit helpers. seats.py exposes seat types and rules, meaning concepts about who may occupy or use access. surface_token.py forwards helpers for surface tokens. Together, these files keep the SDK safe, simple, and stable.

## Files in this stage

### Accounting and Audience Facades
Stable SDK import points for accounting objects and conversation-audience concepts.

### `core/src/ufo/sdk/accounting.py`

`data_model` · `import time / SDK use`

This file is like a labeled service window for the project’s accounting data types. The real accounting definitions live in `ufo.accounting`, but outside users are expected to import public SDK features from named modules under `ufo.sdk`. This module makes that possible for spending reports, usage exports, spending caps, and related totals.

Without this file, a caller who wants to read a workspace spending summary, such as the same rollup shown by `ufoctl spend`, would need to know the project’s internal module layout. That would make outside code more fragile: if internal files move, imports could break. By re-exporting the accounting names here, the project can offer a simpler and more stable public doorway.

The file imports constants and value objects such as `SpendReport`, `AgentSpendReport`, `MemberSpendReport`, and `UsageExport`, then exposes them again with the same names. These are value objects, meaning they mostly represent structured information rather than performing large actions. The `metered_workspaces` helper is also made available through this public module. There are no functions defined here and no runtime process beyond Python loading the imports.


### `core/src/ufo/sdk/audience.py`

`data_model` · `cross-cutting`

This file does not create new behavior of its own. Its job is to present a clean public interface for working with “audiences,” meaning the intended listeners or visibility boundary for a conversation message. Think of it like a reception desk: the real offices are elsewhere, but callers only need one trusted place to ask for the right person.

It re-exports audience-related pieces from `ufo.audience`, including the `Audience` value, constants for shared and foreign audiences, and helper functions for building or reading audience strings. By doing this, the project can keep its internal layout flexible while giving users of the SDK a stable import path: `ufo.sdk.audience`.

Without this file, code outside the core project might need to import directly from internal modules. That would make future refactors riskier, because moving or reorganizing the internal audience code could break users. This small wrapper protects that boundary. It says, in effect: “These are the audience tools we mean to expose publicly.”


### Credential and Authorization Facades
Public re-export modules for auth proxy records, bearer-token verification, credentials, grants, and connection audits.

### `core/src/ufo/sdk/authproxy.py`

`other` · `extension development and import time`

This is a small public “front door” file for authentication proxy support. An authentication proxy is the part an extension can provide when a feed-sync source needs credentials, such as a token or login secret, to talk to an outside provider. Instead of making extension code import from deeper internal modules, this file re-exports the approved names: `AuthProxy`, `Credential`, `DIRECT_ACCOUNT`, and `AuthProxySpec`.

The idea is like a reception desk in a large building. The real offices are elsewhere, but visitors are told to go through one stable desk. Here, the “visitors” are extensions. They can use this file as their stable import path even if the project later rearranges its internal connector code.

The comments explain the bigger flow. An extension declares an `AuthProxySpec` in its manifest and implements an `AuthProxy` backend. If there is only one backend, it can be chosen automatically. If there are several, configuration chooses one. That selected backend resolves credentials for sources marked with `DIRECT_ACCOUNT`, meaning the member supplied credentials directly rather than connecting through a broker account. Sources connected through a broker use the broker’s own credential instead.

There is no runtime logic here. Its value is in keeping the public SDK clean, stable, and clear.


### `core/src/ufo/sdk/bearer.py`

`other` · `request handling and cross-cutting authentication`

This file is a small public doorway into the token verification part of the system. A bearer token is like a temporary pass: a gateway creates it, and another part of the system checks that it is real before trusting what it says. The important safety rule here is that extensions can check the pass, but they do not get the private signing secret used to make passes.

Rather than implementing verification itself, this file re-exports three functions from `ufo.bearer`: `verify_token`, `verified_claims`, and `workspace_claim`. In plain terms, it says: “If you are writing against the SDK, import these token-checking helpers from here.” The actual work stays in the core bearer module, where each function reads `UFO_TOKEN_SECRET` for itself. That keeps the secret centralized and avoids passing it into extension code.

Without this file, callers might import deeper internal modules directly, making the public API less clear and increasing the chance that security-sensitive details leak into the wrong layer.


### `core/src/ufo/sdk/credentials.py`

`other` · `cross-cutting import time`

This file is like a labeled service window for credentials. The real credential logic lives in `ufo.credentials`, but outside code should not have to know or rely on that internal location. Instead, extensions can import from `ufo.sdk.credentials`, which is meant to be the stable public path.

The file exposes a small set of credential-related names: error types for failed or invalid credential work, a `CredentialStore` for working with stored credentials, and helper functions for opening an installation or checking which workspace is allowed for a slot. A “credential” here means sensitive access information, such as a token or secret, and the surrounding code uses these objects to keep that information controlled.

Nothing is calculated here. When Python loads this file, it simply imports the approved names from `ufo.credentials` and makes them available under the SDK namespace. This matters because it creates a clear boundary between public extension-facing API and private project internals. Without this kind of re-export, extension code might import internal modules directly, making it more likely to break when the project is reorganized.


### `core/src/ufo/sdk/grants.py`

`other` · `cross-cutting, when SDK users import grant audit helpers`

This is a small public-facing doorway into the grant-auditing parts of the project. In this codebase, `ufo.sdk` is meant to be the package that outside extension code imports from. However, the project does not put code inside `__init__.py` files, so each public area gets its own named module instead.

This file does not create new behavior. It re-exports selected names from `ufo.grants`: summaries of connections, summaries of grants, and helper functions for turning raw grant information into readable audit views. A “grant” here means permission that lets something access or use something else. A “connection” is the configured link to an external account or service.

The value of this file is stability and clarity. Without it, users would need to know that the real implementation lives in `ufo.grants`, which is an internal-looking location and may change over time. This module acts like a shop window: it displays only the pieces extension authors are expected to use, while the storage room behind it can be reorganized later.


### Seat and Surface Token Facades
Stable SDK doorways for seat governance concepts and surface-token helper functions.

### `core/src/ufo/sdk/seats.py`

`other` · `cross-cutting import-time public API`

This file does not create new seat logic itself. Instead, it re-exports selected names from `ufo.seats`, which is where the real rules and data structures live. Think of it like a labeled shelf in a library: the books are written elsewhere, but this shelf tells outside readers which ones are safe and intended to use.

The project uses `ufo.sdk` as a public software development kit, meaning a stable set of imports for extensions or outside code. Because the package’s `__init__.py` is empty, each public area gets its own named module, such as this one for seats.

The exported items cover seat state, seat snapshots, possible errors like trying to remove the last admin or exceeding a seat limit, and helper functions for seat-reporting jobs such as finding admin conversations or member workspaces. Without this file, extension authors would need to import directly from `ufo.seats`, tying them to the internal layout of the project. This file keeps that boundary clear: core owns the rules, while extensions can decide when to apply them through this public interface.


### `core/src/ufo/sdk/surface_token.py`

`other` · `import time / SDK use`

This module is a small doorway in the public SDK. A “surface token” is a permanent link address that a surface can create and later check, without directly holding the deployment’s secret token key. Instead of making users know the deeper internal module path, this file exposes the needed tools under `ufo.sdk.surface_token`.

The project deliberately keeps `__init__.py` files empty, so public SDK features are placed in named modules like this one. That means this file acts like a labeled shelf in a library: the actual books live elsewhere, but this shelf tells users exactly where to pick them up.

It re-exports `mint_surface_token`, which creates a token, and `verify_surface_token`, which checks one. There are no local functions, classes, or extra decisions here. If this file were removed, existing SDK users who import these token helpers from `ufo.sdk.surface_token` would lose that public import path, even though the underlying implementation might still exist.
