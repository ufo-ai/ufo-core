# Interactive surfaces, web, browser, object, and terminal SDK APIs  `stage-19.3`

This stage is the public front door for extension authors who want to build interactive features. It is shared behind-the-scenes support, not the main work loop itself. Its job is to hide the project’s internal layout and offer stable import paths, so extensions can keep working even if the inside of the system is reorganized.

The browser file exposes approved browser-connection pieces for real-time communication. The HTTP file provides the web route toolbox: requests, responses, forms, uploads, and cookies. The hub file re-exports hub tools, which are used to connect extension code to the system’s shared coordination area. The objects file gathers supported object types and helpers. The sandbox file exposes tools for running code in a controlled, safer space. The surface-token file provides tools for creating and checking permanent links to interactive surfaces. The surfaces file gathers the official building blocks for user-facing extension panels or views. The terminal file exposes the supported terminal API. Together, these files act like labeled sockets on a machine: extensions plug in safely without touching internal wiring.

## Files in this stage

### Web and browser transports
Public import points for extension-facing browser connections and HTTP route helpers.

### `core/src/ufo/sdk/browser.py`

`io_transport` · `cross-cutting browser connection setup and per-turn browser leasing`

This file is a public doorway into the browser side of the system. The project needs a way for extensions to talk to a Chrome browser through CDP, the Chrome DevTools Protocol, which is the remote-control interface Chrome exposes for automation. Instead of making extension code import internal browser machinery directly, this file re-exports the small set of browser-related types that form the supported contract.

The main idea is a lease: for each turn of work, a browser provider can create a `CdpLease`, which gives access to a `CdpEndpoint`. That endpoint contains the browser connection URL and any headers needed to connect. When the turn is over, the lease is released. Some providers may use a local browser, while others may create a fresh hosted browser session each time.

The file also exposes the pieces needed to recover from interrupted work. A lease has a token that can be saved, and a provider can later try to reattach to that same browser session. If the session no longer exists, `SessionGone` signals that clearly. `FileBytes` and `place_file` support opening workspace files in remote browsers only when their contents are actually needed. `FindCompleter` lets the host help rank page elements. Without this file, extensions would have to depend on internal module paths, making the SDK harder to keep stable.


### `core/src/ufo/sdk/http.py`

`io_transport` · `request handling`

This file is a small but important boundary around UFO's web-facing code. Instead of asking extension authors to know that UFO currently uses Starlette, this module re-exports the HTTP types they are allowed to use: `Request` for incoming web requests, response classes for sending pages, JSON, redirects, streams, and plain text, plus form and upload types for submitted files. In plain terms, it is like a front desk: route handlers ask this module for the standard tools, and the module hides where those tools came from.

The file also defines one safety rule for session cookies through `set_session_cookie`. A cookie is a small piece of data a browser stores and sends back later, often used to remember a login or session. This helper makes sure UFO's own session cookies are always set in a narrow, secure way: they are host-only, meaning they do not automatically spread to parent domains or sibling subdomains; they are `HttpOnly`, so page JavaScript cannot read them; and they are `Secure`, so browsers only send them over HTTPS. The only choice callers get is the `SameSite` setting, which controls when browsers include the cookie during cross-site navigation. Without this file, extension code would either depend on internal web-library details or might set cookies in less safe, inconsistent ways.

#### Function details

##### `set_session_cookie`  (lines 23–34)

```
def set_session_cookie(response: Response, name: str, token: str, *, samesite: Literal['lax', 'strict', 'none']) -> None
```

**Purpose**: This is the approved way to attach a session cookie to an HTTP response. It exists to make every UFO-set session cookie secure and limited to the exact host that set it, rather than accidentally allowing it to cover wider domains.

**Data flow**: It receives a response object, a cookie name, a token value, and a `SameSite` choice. It passes those into the response's cookie-setting method while forcing `HttpOnly` and `Secure` to be on and deliberately not setting any domain. Nothing is returned; the response is changed so that, when sent to the browser, it includes the protected cookie.

**Call relations**: When route or session code needs to send a session token to a browser, it should come through this helper instead of calling the lower-level response cookie method directly. This function then hands the final work to Starlette's `Response.set_cookie`, adding UFO's safety defaults before the HTTP response leaves the server.

*Call graph*: 1 external calls (set_cookie).


### Hub, object, and sandbox APIs
Stable SDK doorways for core hub coordination, object access, and sandbox-related capabilities.

### `core/src/ufo/sdk/hub.py`

`other` · `import time / cross-cutting SDK access`

This file is like a clearly labeled shelf at the front of a workshop. The real tools live elsewhere, but this shelf gathers the ones SDK users are meant to reach for. In this project, a “hub” is the part that connects live activity, tool calls, replies, terminal events, costs, and subagent work into one stream of interaction. Those pieces are defined in deeper internal modules such as `ufo.hub`, `ufo.activity`, and `ufo.models.interface`.

Rather than asking extension authors to know those internal paths, this module re-exports the public names from `ufo.sdk.hub`. That makes the SDK easier to use and gives the project room to reorganize internals later without forcing every user to change their imports.

The opening comment also explains an important project rule: package `__init__.py` files are kept empty, so public SDK entry points live in named modules like this one. Without this file, hub extension authors would either import from internal locations directly or lose a convenient public API for building against the `Hub` protocol and related live-frame types.


### `core/src/ufo/sdk/objects.py`

`other` · `cross-cutting import time`

This file does not define new behavior of its own. Instead, it re-exports selected object-related pieces from deeper inside the UFO codebase and presents them as part of the public SDK, which means “software development kit”: the set of tools outside code is meant to use.

The problem it solves is boundary-setting. Extensions need to talk about things like agents, conversations, object owners, object references, object stores, and permission-related errors. But if extensions imported those directly from internal modules, small internal reorganizations could break them. This file acts like a reception desk: outsiders come here for the names they are allowed to use, while the internal layout behind the desk can change more safely.

The imports are written as explicit same-name aliases, such as `ObjectRef as ObjectRef`. That looks repetitive, but it makes the public contract clear to both people and tooling: these names are intentionally exposed here. The opening comment also explains an architectural rule: `ufo.sdk` keeps its package initializer empty, so public SDK surfaces live in named modules like this one rather than in `__init__.py` files.

Without this file, extension authors would either lack a clear supported import path or would have to depend on internal modules, making the ecosystem more fragile.


### `core/src/ufo/sdk/sandbox.py`

`other` · `cross-cutting import-time public API`

This file does not create new behavior of its own. Instead, it gathers important sandbox pieces from deeper inside the project and re-exports them under `ufo.sdk.sandbox`, which is meant to be part of the public developer-facing interface.

The problem it solves is stability and clarity. Extension authors should not need to know the internal folder layout of the project just to describe or use a sandbox backend. This file acts like a shop counter: the actual goods are stored in back rooms such as `ufo.sandbox.session` and `ufo.sandbox.containment`, but users can ask for them from one predictable counter.

The exported items include sandbox session types, command result types, backend connection types, path safety helpers, and constants that describe standard sandbox locations and flags. It also exposes `CarrierSpec` and `Carrier`, which are the pieces an extension uses to declare and implement a sandbox backend. A “sandbox” here means an isolated place where commands can run without freely touching the host system.

This matters because it creates a clean seam between the core system and replaceable sandbox backends. If this file disappeared, outside code might have to import private internal modules directly, making extensions more fragile when the project reorganizes its internals.


### Surface and terminal interfaces
Public SDK entry points for surface links, surface extension building blocks, and terminal APIs.

### `core/src/ufo/sdk/surface_token.py`

`io_transport` · `cross-cutting`

This is a small “front door” module. In this project, `ufo.sdk` is meant to expose public tools through clearly named files, while keeping package `__init__.py` files empty. That rule matters because it prevents hidden startup behavior when someone imports a package. So instead of asking users to reach into internal modules, this file provides a clean public path for token features.

The real work lives in `ufo.surface_token`. This file imports two functions from there and publishes them under the SDK path: one to mint, or create, a surface token, and one to verify, or check, a surface token. A surface token is described here as a permanent link address owned by a surface. The important security idea is that the surface can mint and verify its own links without holding the deploy’s shared token secret. In everyday terms, this module is like a receptionist desk: it does not make the badge itself, but it tells approved callers exactly where to ask for badge creation and badge checking.

Without this file, SDK users would either need to know the internal module layout or import from a less stable location. This keeps the public API tidy and less likely to break when internals move.


### `core/src/ufo/sdk/surfaces.py`

`other` · `cross-cutting import-time API surface`

A “surface” is an outside-facing integration point, such as a chat UI, portal, or other place where users and agents interact. This file does not create new behavior of its own. Instead, it acts like a well-labeled service counter: it collects the approved pieces that surface extension authors are allowed to use and re-exports them from one stable place.

That matters because the real definitions live across several internal modules: surface routing and context types, transcript records, credential request types, blob storage, terminal errors, workspace file limits, and more. Without this file, an extension would need to know the project’s internal layout and import from many different locations. That would make extensions harder to write and more fragile if internal files move.

The opening comment explains the intended use. A surface extension registers a `SurfaceSpec`, which describes its routes and, for durable surfaces, how saved changes are written back. Handler code is typed against privileged objects such as `SurfaceContext`, which gives controlled access to the current conversation, identity, workspace, and writeback data. The repeated `as Name` imports are deliberate: they make these names part of this module’s public interface while keeping the actual implementation elsewhere.


### `core/src/ufo/sdk/terminal.py`

`other` · `import time / public SDK use`

This module does not create new behavior. Its job is to present a clean public face for terminal support in the SDK. A terminal here means an interactive command session, like the shell inside a sandboxed workspace. Code outside the core system can import names such as `TerminalTransport`, `Terminals`, `TerminalOp`, and `TerminalWorkspace` from `ufo.sdk.terminal` without needing to know where those pieces are actually implemented.

The file also re-exports blob storage pieces, `BlobStore` and `BlobNotFound`, because terminal extensions may need to read or write larger data through the fleet's blob store. Think of this file like a reception desk: it does not perform the work itself, but it tells users which official doors to use.

The comment explains an important project rule: `ufo.sdk` keeps its package initializer empty, so public SDK names live in explicit modules like this one. That makes the API clearer and avoids hidden import-time behavior. If this file disappeared, extension authors would have to import from deeper internal modules such as `ufo.sandbox.terminal`, which would make their code more fragile if the internal layout changed.
