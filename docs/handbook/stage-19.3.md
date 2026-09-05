# Public SDK identity, credentials, connectors, and grants  `stage-19.3`

This stage is shared behind-the-scenes support for extensions that need to deal with identity, login, credentials, connectors, and access grants. Its main job is to provide stable SDK import paths, so extension authors do not have to reach into private runtime code that may change.

The files work like a set of labeled service windows. authproxy.py exposes the types needed to add credential backends for feed-sync connectors. bearer.py lets extensions check bearer tokens, which are login tokens sent with requests, without revealing the secret used to make them. connectors.py publishes the connector and OAuth shapes used by integration code. credentials.py exposes approved credential tools. grants.py publishes tools for auditing connector and connection grants, meaning records of who allowed what access. operator.py exposes web-session tools for operator users. surface_token.py lets code create and verify long-lived surface link tokens while keeping the system-wide secret hidden. Together, these files form a safe public boundary around sensitive authentication and access-control internals.

## Files in this stage

### Authentication SDK Entry Points
Stable public imports for extension-facing authentication proxy types and bearer-token verification.

### `core/src/ufo/sdk/authproxy.py`

`data_model` · `extension setup and connector authentication`

This file does not define new behavior. Its job is to make the authentication-extension API easy and safe to find. In this project, a feed-sync connector may need a credential, such as a token or account secret, before it can talk to an outside provider. An extension can provide an auth proxy, which is a small plug-in that knows how to turn a source's account handle into the actual credential the connector needs.

Without this file, extension authors would have to import these pieces from internal runtime paths like `ufo.runtime.access.connectors`. That would make extensions more tightly tied to the project's internal layout. This file acts like a front desk: it points users to the right objects without exposing the corridors behind it.

It re-exports `AuthProxy`, the interface an extension implements; `Credential`, the value a connector receives; `DIRECT_ACCOUNT`, the marker used when a source should be resolved directly through the selected auth backend; and `AuthProxySpec`, the manifest entry that declares the backend. If there is only one backend, it is used automatically. If there are several, configuration chooses one. Sources connected through a broker use the broker's credential instead.


### `core/src/ufo/sdk/bearer.py`

`io_transport` · `request handling`

This file is a small bridge between the project’s internal authentication system and code written by extension authors. A bearer token is a signed proof of identity, like a wristband at an event: the gateway gives it to a user, and other parts of the system can inspect it to decide whether to let the user in. The important safety rule here is that extensions should be able to verify a token, but should not be able to mint new ones or directly hold the signing secret.

To support that, this file does not implement token logic itself. Instead, it publicly re-exports selected names from `ufo.harness.auth.bearer`: paths for login and logout, the session cookie name, and helper functions that verify a token and read claims from it. A claim is a piece of information carried inside the token, such as which workspace the user belongs to.

The comment at the top explains the boundary: token creation belongs to the control plane, while surface extensions only get the “verify half.” The verification functions resolve `UFO_TOKEN_SECRET` themselves, so callers pass in a token and receive checked information back without ever handling the key directly. Without this file, extension code would either need to import from a deeper internal module or duplicate authentication behavior, both of which would make the system harder to keep safe and stable.


### Connector Credentials and Grants
Public SDK surfaces for connector shapes, credential tools, and grant-audit access-control helpers.

### `core/src/ufo/sdk/connectors.py`

`other` · `cross-cutting import-time public API`

This file does not define new behavior itself. Its job is to act like a front desk for connector extensions. A connector is a plug-in that lets the system talk to an outside service, such as a brokered provider with OAuth login, a catalog of available tools or files, and server-side actions. OAuth is the common “sign in and grant access” flow used by many web services.

The real classes, constants, and helper functions live deeper in the project under `ufo.runtime.access.connectors` and `ufo.runtime.access.grants`. This file imports those pieces and immediately re-exports them under `ufo.sdk.connectors`, which is the safer public path for extension authors to use.

The reason this matters is stability. Without this file, extension code would have to depend on internal module paths. If the project later reorganized its internals, those extensions could break. This file is like a shop window: the storage room behind it can change, but customers still know where to find the same products.

The exported pieces describe the connector contract: provider registration, broker tools, catalog pages, credentials, OAuth accounts, grant secrets, staged uploads, workspace bridge helpers, and related error or guidance types. Core code can then drive `/connect`, attach connector access to a tool run, and route feed-sync credentials without knowing each broker’s private mechanics.


### `core/src/ufo/sdk/credentials.py`

`other` · `cross-cutting import-time API exposure`

This file does not create new credential behavior itself. Its job is to re-export a small, approved set of credential-related names from the deeper runtime package. In plain terms, it is like a reception desk: the real offices are elsewhere, but outsiders are told to come here so they do not need to know the building layout.

The exported items include the credential store, validation error types, and helper functions for naming credential objects and reading deployment-environment credential values. By placing them under `ufo.sdk.credentials`, the project gives extension code a cleaner and safer import path. That matters because internal files can move or change over time, while an SDK path is expected to stay more stable.

Without this file, extension authors might import directly from `ufo.runtime.access.credentials`. That would make their code more tightly tied to UFO’s internal structure and easier to break during refactors. This file is therefore small but important: it draws a boundary between the public extension-facing API and the private runtime implementation.


### `core/src/ufo/sdk/grants.py`

`other` · `cross-cutting`

This module is like a clearly labeled service desk window for grant and connection information. The real work lives deeper in `ufo.runtime.access.grants`, but outside code should not need to know that internal path. Instead, extensions can import from `ufo.sdk.grants` and get the approved public objects and helper functions.

The file exposes audit-related items such as summaries of connections, summaries of grants, records of main-agent connections, and an error used when a connection is denied. In plain terms, these names help callers ask questions like: “Which connections were recorded?”, “What permissions were granted?”, and “Was this connection refused because permission was missing?”

The comment at the top explains an important project rule: `ufo.sdk` keeps its package initializer empty, so public SDK features are placed in named modules like this one. That makes the public surface explicit. If this file were missing, extension code would either lose a convenient import path or have to reach into internal runtime modules, which would make future refactoring harder and less safe.


### Operator Sessions
Stable imports for operator web-session tools exposed from the runtime layer.

### `core/src/ufo/sdk/operator.py`

`other` · `cross-cutting`

This file is like a clearly labeled front desk for operator-only web features. Operator-only pages need a shared way to recognize which workspace an operator is trying to access, confirm that the request belongs to the operator domain, bind the shared session cookie after a POST request, and look up operator-facing surfaces in a fleet directory. The actual work lives in `ufo.runtime.ext.operator`, but this file exposes the same pieces through the public SDK path, `ufo.sdk.operator`. That matters because other parts of the project, plugins, or external users can depend on this stable import location instead of reaching into the deeper runtime package. If the runtime layout changes later, this file can keep the public name steady. In practical terms, it re-exports three things: `FleetDirectory`, which is the directory operator surfaces use to index themselves; `bind_operator_session`, which connects the shared session cookie to an operator session; and `resolve_operator_workspace`, which decides which workspace is being requested, including from a `?ws=` query parameter, while applying the operator-domain gate.


### Surface Link Tokens
Public SDK helpers for creating and checking permanent surface link tokens without exposing authentication secrets.

### `core/src/ufo/sdk/surface_token.py`

`io_transport` · `cross-cutting`

This is a small public doorway into the token system. In this project, a “surface” appears to be something that can publish or receive permanent link addresses. Those links need tokens so they can be trusted later, much like a signed ticket proves it was issued by the right booth and has not been changed.

The real work lives in `ufo.harness.auth.surface_token`. This file does not implement token creation or verification itself. Instead, it re-exports two functions, `mint_surface_token` and `verify_surface_token`, under the public `ufo.sdk.surface_token` module. That matters because outside code can import from the SDK path without needing to know the project’s internal folder layout.

The comment also explains an important design rule: `ufo.sdk` uses named modules like this one because the package `__init__.py` files are intentionally empty. That keeps imports predictable and avoids hidden startup code. If this file were missing, SDK users would either have no approved public import path for surface tokens or would need to reach into internal harness code directly, making their code more fragile.
