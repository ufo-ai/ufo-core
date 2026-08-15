# Public SDK connector, credential, and transport facades  `stage-22.3`

This stage is shared behind-the-scenes support for people writing extensions. It does not run the main work itself. Instead, it provides stable “front doors” into the SDK, so outside code can import approved tools without depending on deeper internal paths that may change.

The connector and source doors work together for adding new data inputs. connectors.py exposes the pieces needed to register connector providers and OAuth-style sign-in flows, while sources.py exposes the types and errors used to sync content sources such as REST APIs. authproxy.py and credentials.py cover the handoff of login details and stored credentials, giving extensions only the credential tools they are meant to use. grants.py exposes grant and connection audit types, so extensions can refer to permission records in a stable way. bearer.py exposes token verification only, meaning code can check bearer tokens without gaining access to token creation secrets.

The transport doors cover how extensions talk to users or outside environments. browser.py exposes browser connection interfaces, terminal.py exposes terminal transport and storage pieces, and operator.py exposes helpers for operator-only web sessions.

## Files in this stage

### Credential and authorization facades
Stable SDK import paths for credential handoff, bearer-token verification, and grant or connection audit types.

### `core/src/ufo/sdk/authproxy.py`

`other` · `cross-cutting`

This file exists to make the authentication extension point easier and safer to use. In this project, a connector may need a credential, such as a token or account secret, before it can talk to an outside provider. An extension can contribute an authentication backend by implementing `AuthProxy`, and can describe that backend with `AuthProxySpec` in its manifest. This file gathers those public pieces in one place.

Think of it like a clearly marked service window. The real objects live elsewhere, mostly in `ufo.connectors` and `ufo.ext.manifest`, but extension authors are meant to come through this SDK-facing module instead of reaching into the building’s back rooms.

It also documents the intended behavior. If there is only one authentication backend, the system can use it automatically. If there are several, configuration chooses one through `config.connectors.auth_backend`. That chosen backend resolves credentials for sources marked with `DIRECT_ACCOUNT`, meaning the member provided the provider credential directly. Sources connected through a broker get their credential through that broker instead.

There is no executable logic here. Its value is as a stable public seam: other code can import these names from `ufo.sdk.authproxy` even if the internal module layout changes later.


### `core/src/ufo/sdk/bearer.py`

`util` · `request handling`

This file is a small public doorway into the project’s bearer-token checking code. A bearer token is a short piece of text a client presents as proof that it has already been trusted, much like showing a stamped wristband at an event entrance. Surface extensions need to check these tokens, but they should not know or hold the signing secret used to create them.

To keep that boundary clear, this file simply re-exports selected names from `ufo.bearer`: the login path, the session cookie name, and helper functions that verify a token and read trusted claims from it. The important design choice is that callers do not pass in the secret key. The underlying verification functions look up `UFO_TOKEN_SECRET` themselves. That means extensions can ask, “Is this token valid, and what workspace does it belong to?” without ever being able to mint their own trusted tokens.

Without this file, extensions might import deeper internal modules directly, making the security boundary less obvious and harder to preserve. This file acts like a clearly labeled service window: extensions can check credentials here, but the key stays behind the counter.


### `core/src/ufo/sdk/credentials.py`

`data_model` · `cross-cutting`

This file is like a clearly marked service counter in front of a storage room. The real credential code lives elsewhere, in `ufo.credentials`, but outside extensions should not have to reach directly into that internal area. Instead, this SDK file imports only the approved credential-related objects, errors, and helper functions, then makes them available from a stable public path.

Credentials are sensitive pieces of information or proof, so it matters that the project exposes them carefully. By gathering the allowed names here, the codebase can say, “these are the credential tools extensions may touch.” That includes the credential store, error types for failed or invalid credential work, and helper functions for opening an installation, naming credential objects, and checking which workspace is authorized for a slot.

There is no new behavior in this file. It does not create, check, store, or validate credentials by itself. Its job is boundary-setting: it keeps the public SDK surface tidy and shields outside code from internal layout changes. Without this file, extension code might import from internal modules directly, making it more fragile and harder for the project to reorganize later.


### `core/src/ufo/sdk/grants.py`

`other` · `import time / SDK use`

This module is like a clearly labeled front desk for a few grant-related tools. The real work lives in `ufo.grants`, but outside code should not have to know the internal layout of the project. Instead, extension authors can import from `ufo.sdk.grants`, which is part of the public SDK surface.

The file exposes types and helper functions used to inspect connection permissions and connector grants. In plain terms, these are audit views: they help answer questions like “what connections exist?”, “what grants were given?”, and “was access denied because a permission was missing?”

The comment at the top explains an important project rule: `ufo.sdk` keeps its `__init__.py` empty, so public SDK features live in named modules such as this one. That makes imports explicit and avoids hidden code running just because a package was imported.

If this file were missing, outside users would need to import directly from `ufo.grants`. That would blur the line between internal project structure and the supported SDK API, making future refactors harder and more likely to break extensions.


### Connector and source facades
Public extension entry points for registering connector providers and content sources without depending on internal modules.

### `core/src/ufo/sdk/connectors.py`

`other` · `cross-cutting import-time SDK surface`

This file does not implement connector behavior itself. Instead, it acts like a front desk: outside extensions can import the official connector-related types from here without needing to know where those types live inside the project. That matters because connectors are a seam between the core system and outside brokered services. An extension supplies an OAuth provider, which knows how to start and finish account authorization, and a connector broker, which knows what tools or catalog entries that service offers and how to run them. The core system can then drive the user through connection setup, attach the resulting connector registry to a tool context, and route feed-sync credentials through the same path, without knowing each broker’s private details. By re-exporting names such as ConnectorBroker, ConnectorRegistry, BrokerTool, OAuthProvider, and OAuthAccount, this file gives extension code a single, stable import path: ufo.sdk.connectors. If the internal package layout changes later, this file can preserve the public contract so extensions do not break.


### `core/src/ufo/sdk/sources.py`

`other` · `cross-cutting import-time SDK access`

This file does not implement syncing itself. Instead, it acts like a clearly labeled toolbox at the edge of the project. An extension author can import from this one module instead of learning where every internal class lives.

The concepts it exposes are the pieces needed to build a source: a `SourceBackend`, which is the extension-side object that fetches records and turns them into searchable `Page` documents; `SyncResult`, which tells core what was fetched, deleted, skipped, or fully refreshed; and error types like `CursorExpired`, `StreamSkipped`, and `StreamFault`, which let a connector explain common sync problems in a predictable way.

It also exposes a reusable REST connector framework. A provider that reads from an HTTP API can build on `RestConnector`, describe streams with `StreamSpec`, choose a `Pagination` strategy for moving through pages of API results, and use helper functions such as `get_path` and `records_at` to safely pull records out of nested response data. For sources split into many independent parts, such as repositories or chat channels, it exposes `PartitionWalk`, which centralizes the tricky work of keeping separate cursors.

Without this file, extension code would need to import from internal modules directly, making the public SDK harder to understand and easier to break when internals move.


### Transport and session facades
Stable SDK doorways for browser transports, operator-only web session helpers, and terminal transport APIs.

### `core/src/ufo/sdk/browser.py`

`io_transport` · `cross-cutting`

This file does not create new behavior of its own. Its job is to draw a clean boundary between extension authors and the engine’s browser machinery. In this project, the engine talks to Chrome through CDP, the Chrome DevTools Protocol, which is a way for software to inspect and control a browser. Extensions may need to provide or reconnect to a browser session, but they should not have to import private engine modules directly.

Think of this file like a labeled service window. The real tools live behind the wall in `ufo.browser`, but outsiders are told to come to this window. It exposes names such as `CdpProvider`, which can create a temporary browser connection for a turn; `CdpLease`, which represents that temporary right to use the browser; `CdpEndpoint`, which contains the connection details; and `SessionGone`, which signals that a saved session can no longer be resumed. It also exposes helpers for remote file access and browser element ranking.

Without this file, extension code would either duplicate these definitions or import from internal places that may change. By re-exporting the seam here, the project keeps a stable public contract while leaving the concrete implementation free to evolve elsewhere.


### `core/src/ufo/sdk/operator.py`

`io_transport` · `request handling`

This file is a small public doorway. Other code can import operator web-session tools from `ufo.sdk.operator` without needing to know where the real implementation lives inside the project. The tools it exposes are for an operator-only surface, meaning pages or debug tools meant for trusted operators rather than normal users.

It re-exports two helpers. One resolves which workspace an operator is trying to use, including reading a `?ws=` value from the web request and checking that access is coming through the operator domain. The other binds a shared session cookie after a POST request, so the operator’s browser can keep using the same authenticated session across tools.

The important reason this file exists is stability. Internal code can move around, but external users of the SDK can keep importing from this simple path. Without this file, every debug or operator tool would either need to know the internal module path or would break if that path changed. It is like a signposted front desk: the actual work happens elsewhere, but everyone knows where to ask.


### `core/src/ufo/sdk/terminal.py`

`other` · `import time / SDK use`

This module does not create new behavior. Instead, it gathers important terminal-related names from deeper inside the project and re-exports them under `ufo.sdk.terminal`. In plain terms, it is like a front desk: callers do not need to know which internal hallway contains `TerminalTransport`, `Terminals`, or `BlobStore`; they can come to this one public module.

The file matters because SDK users and extension authors need a stable import path. A terminal-transport extension can implement `TerminalTransport`, reuse the in-process `Terminals` backend, and talk to the system's blob store through `BlobStore`. It also exposes the main terminal operation types and error types, such as `TerminalGone` when a terminal disappears and `TerminalOpFailed` when an operation does not succeed.

The comment explains an important project rule: `ufo.sdk` keeps its package `__init__.py` empty, so public SDK names live in explicit modules like this one. That avoids hidden startup code in package imports while still giving users a clean public surface.
