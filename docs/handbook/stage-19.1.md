# Public SDK facades for extension capabilities  `stage-19.1`

This stage is shared support for extension authors. It is not the main work loop itself; it is the stable front desk they use when adding new abilities to UFO. Each file in ufo.sdk re-exports approved pieces from deeper internal code, so outside extensions can rely on steady import paths even if the inside of the project is reorganized.

The manifest, tools, skills, jobs, and surfaces doorways help an extension describe itself, offer actions, run background work, and connect new user-facing “surfaces,” meaning places where people or other systems interact with UFO. Sources, connectors, authproxy, credentials, and grants support bringing in outside data safely, including login methods, OAuth-style connections, stored secrets, and permission checks. Models, index, memory, and search expose the building blocks for AI model calls, embedding and indexing data, remembering past information, and finding results. Browser, sandbox, and terminal provide controlled access to outside execution environments. Http gives extensions request and response types and safe cookie helpers. Context exposes identity and runtime context, so extensions know who is acting and where.

## Files in this stage

### Access and connection facades
Stable SDK doorways for authentication, identity, credentials, grants, HTTP routing, browser access, and connector setup.

### `core/src/ufo/sdk/authproxy.py`

`data_model` · `extension setup and connector authentication`

This file exists to make authentication extension points easier and safer to use. In this project, a feed-sync connector sometimes needs a credential, such as a token or account secret, before it can talk to an outside provider. An extension can provide an “auth proxy,” meaning a small backend whose job is to turn a source’s stored account choice into the actual credential the connector should use.

The file does not implement that process itself. Instead, it publicly re-exports the important names from their internal homes: `AuthProxy`, `Credential`, and `DIRECT_ACCOUNT` from `ufo.runtime.access.connectors`, plus `AuthProxySpec` from `ufo.runtime.ext.manifest`. Re-exporting means this module acts like a shop window: outsiders can take the approved items from here without walking through the warehouse behind it.

The docstring explains the intended flow. An extension declares an `AuthProxySpec`, gives its backend a name, and implements `AuthProxy`. If there is only one backend, it is chosen automatically. If there are several, configuration chooses one through `config.connectors.auth_backend`. Sources marked with `DIRECT_ACCOUNT` use the selected backend directly. Sources connected through a broker get their credential from that broker instead.

Without this file, extension authors would need to know and import internal connector modules directly, making plugins more fragile when the internal layout changes.


### `core/src/ufo/sdk/browser.py`

`other` · `cross-cutting`

This file does not implement browser behavior itself. Instead, it re-exports a small set of browser-related interfaces from `ufo.browser` as part of the public SDK. Think of it like a reception desk: outsiders come here for the official names, while the real machinery stays behind the door.

The main idea is to keep a clean boundary between extensions and the engine. An extension can provide or use a `CdpProvider`, which supplies access to Chrome through CDP, the Chrome DevTools Protocol — the control channel used to inspect pages, click things, and read browser state. A provider creates a temporary `CdpLease` for a turn of work. That lease gives a `CdpEndpoint`, meaning the browser connection URL and any needed headers. When the turn ends, the lease can be released.

The file also exposes supporting pieces: `SessionGone` signals that an old browser session cannot be recovered, `FileBytes` represents a lazy way to read file contents only when needed, and `FindCompleter` is a hook for ranking page elements found by the browser engine.

What matters is the separation. Extensions depend on this SDK-facing file, not on internal browser code. That makes the browser connection contract clearer and easier to keep stable.


### `core/src/ufo/sdk/connectors.py`

`other` · `import time and extension integration`

This file does not define new behavior. Its job is to make the connector system easier and safer to use from outside the core codebase. A connector is an add-on that lets the system talk to an outside brokered service, such as a search provider, file provider, or tool provider. OAuth is the common web sign-in flow where a user grants access without sharing their password.

The concrete code lives deeper inside the project, mainly in `ufo.runtime.access.connectors` and `ufo.runtime.access.grants`. This file re-exports the important public names from those modules. In everyday terms, it works like a reception desk: instead of sending every visitor through the building to find the right office, it gives them the approved public forms and contacts in one place.

That matters because extensions need a stable contract. They can build a `ConnectorProvider`-style integration using the broker interfaces, OAuth provider interfaces, registry types, catalog entries, request forwarding pieces, and stale-grant guidance exposed here. Meanwhile, the core system can keep its internal layout flexible. If outside code imported directly from internal modules, future refactors could break extensions more easily.

Without this file, extension authors would need to know where the internal connector and grant classes live, and the project would lose a clear public seam between the SDK and the implementation.


### `core/src/ufo/sdk/context.py`

`other` · `cross-cutting import-time SDK access`

This file does not define new behavior of its own. Instead, it acts like a front desk for the SDK: extension code can import context-related tools from `ufo.sdk.context` without needing to know where those tools live inside the project.

The re-exported items describe and provide access to things an extension may need while it is running: the current agent identity, conversation facts, page and source records, model and credential access, scoped storage, trajectory information, and references to agent or proposal changes. A “scoped” context means information is limited to the current agent, conversation, request, or workspace rather than being global everywhere.

This matters because internal project structure can change over time. Without a file like this, extension authors would have to import directly from many lower-level modules such as `ufo.runtime.ext.context`, `ufo.runtime.agent_scope`, or `ufo.schema.records`. That would make extensions more fragile. By re-exporting the public pieces here, the project gives outside users a clearer and more stable import path.

There is one important behavior to notice: each import uses `as` with the same name. That is not changing the value; it is making the re-export explicit for type checkers and documentation tools, so these names are clearly part of this module’s public surface.


### `core/src/ufo/sdk/credentials.py`

`other` · `cross-cutting`

This file does not create new credential behavior itself. Its job is to make a safe, stable public surface for extensions. Instead of asking extension authors to import from deeper internal modules, it re-exports the credential objects, errors, and helper functions they are allowed to touch.

Think of it like a reception desk in a large office building. The real work happens in offices deeper inside, but visitors are directed to one clear desk where the approved forms and contacts are available. Here, the deeper office is `ufo.runtime.access.credentials`, and this SDK file is the public desk.

The names it exposes include error types for failed or invalid credential operations, a credential store abstraction, and helper values or functions related to deployment environments, installation access, workspace authorization, and credential object naming. The repeated `as same_name` imports make the re-export explicit: these names are intentionally part of this module’s public contract.

Without this file, extension authors would need to depend directly on internal package paths. That would make extensions more fragile, because internal modules can change more freely than the SDK. This file helps keep the outside-facing API clear and stable.


### `core/src/ufo/sdk/grants.py`

`io_transport` · `cross-cutting`

This module is like a clearly marked service counter in front of a back office. The real work for tracking connection permissions and grant summaries lives in `ufo.runtime.access.grants`, but outside code should not need to know that internal path. Instead, this file exposes the approved public names through `ufo.sdk.grants`.

That matters because extensions need a safe way to inspect things such as which connections were recorded, which permissions were denied, and how grants can be summarized. If every extension imported directly from the internal package, the project would be harder to reorganize later without breaking users. By re-exporting only selected classes and functions here, the project creates a small public contract: these are the grant-related tools extension code is meant to use.

The file also reflects a project rule mentioned in the comment: package `__init__.py` files stay empty, so public SDK features live in named modules like this one. In short, this file is not a worker; it is a signpost and protective layer that points users to the right grant-audit objects while hiding the internal layout.


### `core/src/ufo/sdk/http.py`

`io_transport` · `request handling`

This file acts like a small, approved counter where extensions pick up their HTTP tools. Instead of reaching directly into Starlette, the underlying web framework, route handlers can import `Request`, `Response`, `HTMLResponse`, `JSONResponse`, `StreamingResponse`, uploaded-file types, and form-error types from `ufo.sdk.http`. That keeps the public route interface stable and makes it clear which HTTP pieces extensions are expected to use.

The file also protects session cookies, which are the browser-side tokens used to remember who a user is. Cookies can be risky if they are sent too widely or over an unsafe connection. The helper `set_session_cookie` deliberately does not accept a cookie domain, so cookies stay tied to exactly the current host instead of spreading to parent domains or sibling subdomains. It also always marks cookies as `HttpOnly`, meaning browser scripts cannot read them.

The other helper, `cookie_secure`, decides whether a cookie should be marked `Secure`, which tells the browser to send it only over HTTPS. Importantly, it bases this on the public scheme the site says it is published under, not on the internal request that reaches the app. That matters behind load balancers or ingress systems, where public HTTPS traffic may arrive at the app as plain HTTP internally.

#### Function details

##### `cookie_secure`  (lines 23–32)

```
def cookie_secure(published_scheme: str) -> bool
```

**Purpose**: This function decides whether a session cookie should be marked `Secure`, meaning the browser should only send it over HTTPS. It uses the published public scheme of the surface, because the internal request may look like plain HTTP even when users are visiting through HTTPS.

**Data flow**: It receives a scheme string such as `http` or `https`. It checks whether that string is exactly `http`. If it is, it returns `False`; for anything else, it returns `True`, so cookies stay protected unless the surface explicitly says it is published as plain HTTP.

**Call relations**: This is the decision helper that callers are expected to use before binding a session cookie. It does not call other project code; it simply turns the published scheme into the `secure` choice that is then passed to `set_session_cookie`.


##### `set_session_cookie`  (lines 35–54)

```
def set_session_cookie(response: Response, name: str, token: str, *, samesite: Literal['lax', 'strict', 'none'], secure: bool) -> None
```

**Purpose**: This is the approved way to attach a UFO session cookie to an HTTP response. It keeps the cookie host-only, forces it to be unreadable by browser scripts, and lets the caller choose only the safe policy options that are meant to vary.

**Data flow**: It receives a response object, a cookie name, a token value, a `SameSite` setting, and a `secure` flag. It passes those values to the response’s cookie-setting method, while always adding `httponly=True` and never adding a domain. The response is changed in place so that, when sent to the browser, it includes the session cookie.

**Call relations**: Route or session code calls this when it needs to bind a browser session to a response. This function then hands the actual header-writing work to Starlette’s `Response.set_cookie`, while narrowing how it can be used so callers cannot accidentally create wider or less protected session cookies.

*Call graph*: 1 external calls (set_cookie).


### Extension declarations
Public facades for declaring extension manifests, background jobs, skills, and tools.

### `core/src/ufo/sdk/jobs.py`

`other` · `extension import and job declaration`

This file is intentionally thin: it does not implement job behavior itself. Its job is to make the project’s public extension interface safer and easier to use. Instead of asking extension authors to know where job helpers live inside the core code, it gathers the allowed names in one stable place: `ufo.sdk.jobs`.

The re-exported pieces help extensions describe background work in terms the core dispatcher can understand. For example, `JobSpec` is the public shape for declaring a job. `owner_candidates` and `WorkspaceCandidates` help a job say which workspaces have work waiting. A workspace is a separate area of data, and these helpers let the core ask, on each scheduler tick, “which workspaces should this job run for right now?” That matters because due work can depend on the current time.

The file also exposes context helpers, such as finding workspaces tied to live agents, seated members, connections, unseeded agents, or untitled conversations. Finally, it re-exports `PAGE_CHANGE_CURSOR_KEY`, the shared prefix used for stored page-consumer progress, so extensions can reset that progress without guessing the core’s private storage naming rules.

Without this file, extension code would likely depend on internal module paths and hidden conventions, making extensions more fragile when the core is reorganized.


### `core/src/ufo/sdk/manifest.py`

`other` · `cross-cutting import time`

This file does not create new behavior. Its job is to make the project easier and safer to build on from the outside. Extensions need to describe what they provide: tools, hooks, credentials, setup steps, conversation slots, image previews, workspace changes, and other manifest pieces. Those pieces are actually defined in deeper internal modules, but extension authors are told to import them from `ufo.sdk.manifest` instead.

Think of it like a hotel front desk. The rooms and staff are elsewhere in the building, but guests should go to one clear place for help. In the same way, this file re-exports selected names from internal modules under a public, stable address.

That matters because internal code can move around over time. If every extension imported directly from `ufo.runtime.ext.manifest`, `ufo.runtime.media.image_previews`, or other internal locations, a harmless cleanup could break outside users. By routing imports through this file, the project can keep a cleaner boundary: extension authors use the SDK path, while maintainers can reorganize internals more freely.

The repeated `as Name` imports are intentional. They make the public exported names explicit and clear to readers and tools. There are no functions here because the file is a catalog, not a machine that performs work.


### `core/src/ufo/sdk/skills.py`

`other` · `import time / cross-cutting public API`

This module is like a clearly labeled front desk for the skill system. The real skill logic lives in internal packages such as `ufo.runtime.skills.runtime` and `ufo.runtime.skills.selection`, but callers using the public `ufo.sdk` interface should not need to know that internal layout. Instead, they can import skill-related pieces from `ufo.sdk.skills`.

It exposes the main value objects used to describe skills, such as `RuntimeSkill` and `SkillCard`. It also exposes `parse_skill_content`, which turns in-memory skill text into structured skill data, and `skill_root`, which points to the root location used for skills. From the selection side, it exposes `SKILL_LINE_MAX_CHARS`, a limit used when reading or scoring skill text, and `lexical_score`, a simple text-matching scorer used when ranking skill search results.

The comment at the top explains an important project rule: package `__init__.py` files must stay empty, so public imports are gathered in named modules like this one instead. Without this file, users of the SDK would either have to import from internal modules directly, making their code more fragile, or the project would lose a clean public entry point for skill features.


### `core/src/ufo/sdk/tools.py`

`other` · `cross-cutting: used whenever extensions import SDK tool types or task helpers`

This file does not create new behavior of its own. Instead, it gathers the tool-related pieces that outside extensions are meant to use and re-exports them under `ufo.sdk.tools`. In plain terms, it is like a labeled front desk: extension code can ask here for the official forms and helpers, without needing to know which back-office folder each one really lives in.

That matters because internal code can move around over time. If extensions imported directly from deep internal paths, small reorganizations could break them. By keeping this public module stable, the project gives tool authors a safer contract: import `ToolDef`, `ToolContext`, `ToolResult`, task helpers, file-change limits, content types, and access-related errors from here.

One especially important export is `run_task`, along with task handles and timeout helpers. These support long-running detached commands, such as shell commands started by the built-in bash tool. If a command takes longer than the caller can wait, it can keep running in the background and still be found later through the same task tracking system.

Because the project forbids executable code in package `__init__.py` files, named modules like this one become the official public surface of the SDK.


### Model and retrieval integrations
SDK import points for indexing, memory search, model clients, and general search capabilities.

### `core/src/ufo/sdk/index.py`

`data_model` · `cross-cutting`

This file exists to give extension authors a stable, simple place to import the pieces needed to build an index backend. An index backend is the part of the system that stores searchable chunks of text and later finds matching chunks, using ordinary keyword-style search, vector search, or both. A vector is a list of numbers that represents the meaning of text, so similar ideas can be found even when the words are different.

The file does not define new behavior itself. Instead, it re-exports selected names from `ufo.runtime.indexing`. This is like a reception desk: outsiders come here for the approved forms and tools, while the internal office layout can change behind the scenes.

The exported pieces include the main shapes used for indexing, such as `Chunk` for text pieces, `Hit` for search results, `IndexScope` for deciding what to delete or limit, and `IndexBackend` for the backend interface an extension must implement. It also exposes `EmbedClient`, the interface for turning batches of text into vectors, plus `TextChunker` and `chunk_embed_upsert`, which help split text, embed it, and store it.

Without this file, extension code would have to depend directly on internal module paths. That would make plugins more fragile whenever the core project reorganizes its internals.


### `core/src/ufo/sdk/memory.py`

`data_model` · `cross-cutting`

This file is like a front desk for memory-search features. The real definitions live elsewhere, in `ufo.runtime.memory`, but outside code should not need to know that internal location. Instead, provider extensions can import from this SDK file, which is meant to be the public, supported doorway.

It exposes three things: `MemorySearchProvider`, which describes the shape of a component that can search memory; `MemoryMatch`, which represents one search result; and `DEFAULT_MEMORY_SEARCH_PROVIDER`, which names the built-in default provider. By re-exporting them here, the project can keep a cleaner boundary between internal code and extension-facing code.

Without this file, extension authors might import directly from internal modules. That would make their code more fragile, because internal paths can change as the project grows. This small file helps keep the public contract steady, even if the implementation behind it moves later.


### `core/src/ufo/sdk/models.py`

`other` · `cross-cutting; active when SDK users import model-related public APIs`

This file does not define new behavior of its own. Instead, it gathers the model-facing building blocks from deeper inside the project and re-exports them as part of the SDK, which is the supported interface for extension authors.

The problem it solves is stability. Without this file, outside code would need to import directly from internal modules such as the OpenAI, Anthropic, interface, pricing, and spec packages. If those internal modules were reorganized later, extensions could break even if the actual concepts stayed the same. This file acts like a reception desk: outsiders ask here for the public names they are allowed to use, and the file points them to the real implementation behind the scenes.

The exported names cover several model-related concerns: clients for calling providers such as OpenAI and Anthropic, common request and response shapes, message and content block types, tool-use structures, streaming event types, image trimming helpers, model pricing records, and model capability descriptions. The repeated “as same name” imports make the public API explicit: these are intentionally available through `ufo.sdk.models`.

In short, this is an SDK seam. It keeps extension code cleaner, gives the project maintainers room to change internals, and documents which model-related pieces are safe for external use.


### `core/src/ufo/sdk/search.py`

`other` · `cross-cutting`

This file is a small public bridge for the search system. In this project, an extension can provide a search backend: a piece of code that receives a search request and returns search results, and sometimes can also fetch a full page afterward. The actual definitions live in `ufo.runtime.search`, but this file re-exports them through `ufo.sdk.search`, which is the safer public path for outside code to use.

Think of it like a reception desk. The real offices are elsewhere, but visitors are told to come through one clear entrance. That matters because the project can reorganize its internal modules later while keeping this public import path the same.

The names exposed here describe the search contract. `SearchProvider` is the interface a backend implements. `SearchQuery` is the question being asked. `SearchResults` and `SearchHit` describe the answers. `FetchRequest` and `FetchedPage` cover the optional follow-up step where a provider retrieves a page's contents. Without this file, extension authors would need to depend on internal module paths, making their code more fragile when the project changes.


### Runtime and surface capabilities
Public SDK facades for sandbox execution, source sync, UI or system surfaces, and terminal access.

### `core/src/ufo/sdk/sandbox.py`

`other` · `cross-cutting import-time SDK surface`

This module is a signpost and front desk, not a place where new sandbox behavior is implemented. The project keeps its public SDK surface in named modules like this one, because package initializer files are intentionally kept empty. That means someone writing an extension can import from `ufo.sdk.sandbox` and get the pieces they need without reaching into private internal modules.

The sandbox is the controlled environment where commands, files, browser tools, and network access can run safely and consistently. This file re-exports the main sandbox protocol and value objects, such as `Carrier`, `Sandbox`, `SandboxSession`, `SandboxSpec`, and `ExecResult`. It also exposes standard paths, environment names, user IDs, proxy settings, and containment helpers that keep file access inside allowed boundaries. In plain terms, these names are the shared vocabulary between the core system and a deploy-specific backend that actually provides the sandbox.

Without this file, extension authors would need to import from many lower-level modules directly. That would make their code more fragile, because internal files could move or change. This file works like a stable reception desk: the rooms behind it may be rearranged, but visitors still come to the same counter.


### `core/src/ufo/sdk/sources.py`

`util` · `cross-cutting: used when extensions are imported and when source backends are built`

This file does not implement source syncing itself. Instead, it acts like a clearly labeled toolbox shelf for extension developers. Rather than asking an extension to import pieces from many internal modules, it re-exports the approved building blocks from `ufo.runtime.sources` under the SDK path `ufo.sdk.sources`.

The main idea is that an extension can provide a `SourceBackend`, which knows how to fetch records from an outside provider and turn them into `Page` documents. Core can then poll that source, store the fetched pages in memory, and decide what changed. The file also exposes the result and error shapes that let a backend explain what happened: a full snapshot, an incremental update, deleted pages, an expired cursor, a skipped stream, or a provider response that could not be understood.

For REST-based services, this file also exposes connector helpers such as `RestConnector`, `StreamSpec`, `Pagination`, and record-shaping utilities like `records_at` and `get_path`. These give extension authors a reusable pattern for walking through paginated web API responses, including partitioned streams such as “one cursor per repository” or “one cursor per channel.”

Without this file, extension code would need to depend directly on deeper internal module paths. That would make extensions more fragile, because internal organization could change. This file creates a safer, simpler public seam.


### `core/src/ufo/sdk/surfaces.py`

`other` · `cross-cutting import-time public API`

This file does not define new behavior of its own. Instead, it acts like a labeled shelf in a toolkit: if an extension author needs the pieces for a surface integration, they can import them from `ufo.sdk.surfaces` instead of hunting through many internal modules. A “surface” here means an external-facing place where conversations, messages, credentials, terminal output, files, or writeback results can be delivered and received.

The imports cover several kinds of things: specifications for registering a surface, context objects that tell a handler what conversation or workspace it is working in, route and authentication types, error types, transcript and turn records, credential request records, terminal and workspace file support, and helper functions for formatting or recognizing special message content. Some names also expose constants, such as markers for attachments or silence sentinels.

The main reason this file matters is stability. Internal code can move around, but extension authors should have a simple and predictable place to import the official surface API. Without this file, outside integrations would need to depend directly on deeper project paths, making them more fragile when the project is reorganized.


### `core/src/ufo/sdk/terminal.py`

`other` · `cross-cutting`

This module does not define new behavior. Instead, it gathers and re-publishes the pieces needed to build or use terminal transport support in the UFO SDK. Think of it like a labeled shelf at the front of a workshop: the tools are made elsewhere, but this shelf tells outside users which tools are meant for them.

The SDK keeps its package initializer files empty, so public imports live in named modules like this one. Here, terminal-related names are pulled from their real homes and exposed under `ufo.sdk.terminal`. These include the `TerminalTransport` interface for connecting terminal implementations, the in-process `Terminals` backend, operation and workspace types, timing constants, and terminal-specific error classes such as `TerminalAbsent`, `TerminalGone`, and `TerminalOpFailed`. It also re-exports `BlobStore` and `BlobNotFound`, because terminal extensions may need to reach the fleet's blob storage.

Without this file, users would need to know the internal layout of the project and import from lower-level modules directly. That would make external code more fragile, because internal paths can change. This file gives callers a small, intentional public surface for terminal SDK work.
