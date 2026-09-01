# Public SDK Extension Authoring Facades  `stage-18.2`

This stage is the project’s public front door for extension authors. It is not the main work loop itself. Instead, it is shared support used while people build add-ons for UFO. A “facade” here means a stable, simple import path that hides the messy internal folder layout, like a service desk that fetches the right specialist for you.

Each file opens one safe doorway. connectors.py exposes connector and OAuth building blocks for linking outside services. context.py provides the approved context objects extension code can use to understand where it is running. credentials.py gives controlled access to credential helpers. http.py supplies route authors with request, response, upload, form, and cookie tools without forcing them into lower-level web code. jobs.py exposes background job tools. manifest.py provides the types and constants used to describe an extension. sources.py gathers source-sync, REST connector, pagination, and sync result pieces. surfaces.py supports channels such as chat, inboxes, or terminals. tools.py exposes the approved pieces for defining UFO tools. Together, these files keep extensions stable even if the core internals move around.

## Files in this stage

### Connector and Context Foundations
Stable SDK facades for the basic extension-facing connector, context, and credential types that authors import before implementing behavior.

### `core/src/ufo/sdk/connectors.py`

`other` · `cross-cutting import-time API surface`

This file does not implement connector behavior itself. Instead, it acts like a clearly labeled front desk for the connector system. Extensions can import connector-related building blocks from here, while the real definitions live deeper in the runtime package.

The problem it solves is stability and separation. A connector extension needs to describe two main things: how a user grants access through OAuth, which is a common web sign-in permission flow, and how the connector broker lists available tools, runs broker-side actions, and supplies credentials for feed syncing. If extensions imported those details directly from internal modules, every internal refactor could break them. This file gives them one supported place to import from.

The re-exported names cover the main pieces of the connector seam: provider and account types for OAuth, broker and registry types for discovering and running connector tools, catalog and file/search shapes for describing available resources, request-forwarding helpers, staged uploads, and errors or guidance for unusable grants.

In everyday terms, this file is a menu, not the kitchen. It tells outside code which connector ingredients are officially available, while keeping the cooking in `ufo.runtime.access.connectors` and `ufo.runtime.access.grants`.


### `core/src/ufo/sdk/context.py`

`other` · `cross-cutting import-time SDK access`

Extension authors need a safe and predictable way to learn about the current agent, conversation, pages, stored data, credentials, and model access. The real implementations live in internal runtime and schema modules, but exposing those internal paths directly would make extension code fragile. If the project later reorganized its internals, every extension might have to change its imports.

This file solves that by acting like a reception desk. Instead of sending outside code through many back hallways, it presents one clear counter: `ufo.sdk.context`. It imports selected public classes, records, helper objects, and exceptions, then re-exports them under the same names. Examples include `ExtensionContext`, which represents what an extension can see and do during a run; `CredentialAccess`, which describes controlled access to secrets; `Trajectory`, which relates to the path of a conversation or task; and records such as `PageRecord`, `SourceRecord`, and `ProposalRef`.

There is no active logic here. Importing this file simply makes these names available. Its importance is in keeping the SDK boundary clean: extension handlers can depend on this file as a stable contract, while the system remains free to organize its internal runtime code differently.


### `core/src/ufo/sdk/credentials.py`

`other` · `cross-cutting; active when SDK users import credential tools`

This module exists to keep a clean boundary between the public SDK and the project’s internal credential machinery. Credentials are sensitive pieces of information or proof, so extensions should only touch the approved shapes and helper functions. Instead of asking extension code to reach into `ufo.runtime.access.credentials`, this file re-exports the allowed names from one stable place: `ufo.sdk.credentials`.

Think of it like a reception desk in a secure building. The real offices are deeper inside, but visitors are given a clear, safe counter where they can request the things they are allowed to use. If the internal layout changes later, this SDK file can keep the outside-facing import path the same.

The exported items include credential error types, a credential store type, validation-related errors, and helper functions for specific credential sources or targets such as deployment environment credentials, installation access, workspace authorization, and credential object naming. The file does not add new behavior of its own. Its importance is in API design: it tells extension authors, “these are the credential tools you may depend on,” while hiding the rest of the runtime internals.


### Runtime Interaction Facades
Public SDK entry points for extensions that interact with HTTP traffic or schedule background work.

### `core/src/ufo/sdk/http.py`

`io_transport` · `request handling`

This file acts like a small front desk for HTTP work in the UFO SDK. A route handler receives a Request and sends back a Response, and this module re-exports the approved Starlette response classes such as HTMLResponse, JSONResponse, RedirectResponse, and StreamingResponse. It also exposes UploadFile for incoming file uploads and FormParserError for bad multipart form data, so extension authors can catch the right error when a browser submits a broken form.

The only real behavior in this file is about session cookies. Cookies are security-sensitive because they often prove that a browser is logged in. The helper cookie_secure decides whether a cookie should use the Secure flag, which tells browsers to send it only over HTTPS. Importantly, it bases this on the public scheme the site is published under, not on the internal request scheme. That matters when HTTPS is ended by a front proxy and the app itself only sees plain HTTP.

The helper set_session_cookie is the single approved way to set UFO session cookies. It always makes cookies HttpOnly, meaning browser JavaScript cannot read them, and it never allows a domain to be supplied, so the cookie stays tied to exactly one host. This prevents a cookie from accidentally leaking across subdomains, preview sites, or environments.

#### Function details

##### `cookie_secure`  (lines 23–32)

```
def cookie_secure(published_scheme: str) -> bool
```

**Purpose**: This function answers one safety question: should the session cookie be marked Secure? It returns false only when the published site scheme is exactly plain HTTP, because browsers will drop Secure cookies from a plain-HTTP origin.

**Data flow**: It receives the scheme the surface says it is published under, such as "http" or "https". It compares that text to "http". If it is exactly "http", the result is false; for anything else, the result is true.

**Call relations**: This is a public SDK helper for code that is about to set a session cookie. The call graph shown here does not show an internal caller, but its result is meant to be passed as the secure setting to set_session_cookie so every caller makes the same HTTPS-versus-HTTP decision.


##### `set_session_cookie`  (lines 35–54)

```
def set_session_cookie(response: Response, name: str, token: str, *, samesite: Literal['lax', 'strict', 'none'], secure: bool) -> None
```

**Purpose**: This function is the approved way to attach a session cookie to an HTTP response. It enforces the project’s safety rules: the cookie is HttpOnly, host-only, and uses caller-chosen SameSite and Secure settings.

**Data flow**: It receives a response object, the cookie name, the session token, a SameSite policy, and whether the cookie should be Secure. It calls the underlying response’s cookie-setting method with those values, adds HttpOnly automatically, and deliberately does not pass any domain. It returns nothing, but the response is changed so it will send a Set-Cookie header to the browser.

**Call relations**: When route or surface code needs to bind a session token to a browser, it should come through this helper instead of calling the web framework directly. Inside, this function hands the final work to Starlette’s Response.set_cookie, but only after applying UFO’s fixed safety choices.

*Call graph*: 1 external calls (set_cookie).


### `core/src/ufo/sdk/jobs.py`

`other` · `cross-cutting; used when extensions are imported or define background jobs`

This is a thin public doorway into the job system. Extensions can define background jobs, choose which workspaces those jobs should run for, and work with job-related cursor keys without importing from deeper runtime modules. That matters because internal code can move around, but this public SDK module can stay stable for extension authors.

The file does not create new behavior of its own. Instead, it re-exports selected names from internal runtime modules. Think of it like a labeled service counter: the actual workers are in the back room, but outside users only need to know which counter to visit.

The most important exported pieces are `JobSpec`, which describes a background job, and `owner_candidates`, which helps an extension say, “these are the workspaces where my job currently has something to do.” That workspace selection is built fresh each tick, meaning each scheduler pass can use the current time or current database state. The file also exposes helpers for common workspace groups, such as workspaces with live agents or seated members.

It also re-exports `PAGE_CHANGE_CURSOR_KEY`, a shared prefix used when remembering how far a page-change consumer has progressed. By importing that constant here, extensions do not have to copy or guess core’s internal storage key format.


### Manifest and Source Facades
Authoring facades for declaring extension metadata and implementing source synchronization behavior.

### `core/src/ufo/sdk/manifest.py`

`other` · `import time / extension loading`

This file does not define new behavior. Its job is to gather many manifest-related names from inside the runtime and present them in one safe, public place: `ufo.sdk.manifest`. A manifest is the description an extension gives the system: what tools, hooks, credentials, conversation slots, setup steps, and other capabilities it wants to provide or use.

The reason this file matters is stability. Internal modules can move around as the project changes, but extension authors should not have to chase those moves. This file works like a reception desk in a large building: visitors ask at the front desk instead of wandering through private offices. The front desk points them to the right things while hiding the building layout.

It re-exports items for credentials, conversation slots, extension manifests, agent setup, agent specs, image previews, and workspace changes. The repeated `as SameName` imports make the public names explicit. Because the project bans code inside package `__init__.py` files, named modules like this one become the official public surface. If this file were missing, extension code would likely import from runtime internals directly, making extensions more fragile and harder to keep compatible.


### `core/src/ufo/sdk/sources.py`

`other` · `cross-cutting import/API surface`

This file does not define new behavior. Its job is to make the source-extension API easy and safe to use from outside the core runtime. Think of it like a front desk: the real workers live in deeper modules, but this file tells extension authors, “Use these names; this is the supported entrance.”

A source extension is code that knows how to read records from an outside system, such as a chat service, mail system, repository host, or any REST API. The key idea is that an extension turns provider records into `Page` objects, which are the documents core can store, search, and later embed. This file re-exports the main contract, `SourceBackend`, along with result and error types such as `SyncResult`, `CursorExpired`, `StreamSkipped`, and `StreamFault`.

It also exposes a reusable REST connector framework. Instead of every extension inventing its own looping, pagination, cursor, and partition logic, extension authors can subclass `RestConnector`, describe streams with `StreamSpec`, choose a `Pagination` strategy, and rely on shared helpers like `records_at` and `get_path` to pull records out of API responses.

Without this file, extensions would need to import from internal runtime modules directly. That would make extension code more fragile, because internal module paths could change even if the public source API stays the same.


### Surface and Tool Facades
Stable import points for outward-facing surface integrations and UFO tool definitions.

### `core/src/ufo/sdk/surfaces.py`

`other` · `cross-cutting: used when surface extensions import the public SDK API`

This file is like a front desk for the surface extension API. The real code lives in deeper internal modules, but outsiders writing an extension should not have to hunt through those internals or depend on their exact locations. Instead, they can import from `ufo.sdk.surfaces` and get the pieces they need.

A “surface” is an outside place where the system can receive messages, send replies, show files, ask for credentials, or connect to services. This file re-exports the main building blocks for that work: `SurfaceSpec` describes what an extension offers, `SurfaceRoute` describes where messages should go, `SurfaceContext` gives trusted access to runtime services, and `Writeback` represents the information a durable surface writes back after delivery. It also exposes related models such as conversations, turns, credentials, connection requests, workspace files, terminal frames, and shared artifacts.

There is no new behavior here. The repeated `as Name` imports are deliberate: they make these names part of this module’s public API. Without this file, extension authors would need to import many pieces from internal modules, which would make their code more fragile whenever the project reorganizes its internals.


### `core/src/ufo/sdk/tools.py`

`other` · `cross-cutting`

This module is like a shop counter: extension code comes here to pick up the official objects it is allowed to use, instead of wandering into the storage room of internal runtime modules. It does not create new behavior of its own. Its job is to re-export selected classes, constants, and helper functions from deeper parts of the system under the public `ufo.sdk.tools` name.

That matters because tools are written by extensions, and extensions need a stable contract. Internal files can be reorganized, renamed, or changed, but this SDK layer can stay consistent. Without a file like this, extension authors would have to depend on implementation details, making their tools more fragile.

The exported names cover the main pieces a tool needs: `ToolContext` and `ToolResult` for receiving request information and returning results; `TextContent` and `ImageContent` for response content; tool registry types such as `ToolDef`, `ActionBinding`, and `ObjectBinding` for declaring tools; access and preview types such as `ConnectUnavailable` and `StoredPreview`; and task helpers such as `run_task`, `TaskRun`, `task_handles`, and timeout settings for long-running commands. The comments call out `run_task` as especially important: it lets command-style tools keep running in the background after the original caller’s time budget is exceeded, while still being tracked through shared task handles.
