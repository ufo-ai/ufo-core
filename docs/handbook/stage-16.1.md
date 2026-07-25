# Extension declaration and handler SDK contracts  `stage-16.1`

This stage is shared behind-the-scenes support for people who build extensions. It defines the public “contract” between an extension and the UFO core: what an extension can declare, and what kinds of objects its code is allowed to use when UFO calls it.

The main declaration piece is `ext/manifest.py`. It describes the extension “menu”: tools, web routes, background jobs, credentials, model and search backends, skills, schedules, surfaces, and startup or shutdown hooks. The SDK files then provide stable front doors for extension authors. `sdk/manifest.py`, `sdk/jobs.py`, `sdk/scheduling.py`, `sdk/skills.py`, `sdk/surfaces.py`, and `sdk/tools.py` re-export approved types so outside code does not rely on private internal paths. `sdk/context.py` exposes the context objects passed into handlers, which are like the work order and toolbox for a request. `sdk/http.py` supplies safe request, response, and session-cookie helpers, keeping web behavior and security rules consistent. Together, these files make extensions predictable to load, call, and maintain.

## Files in this stage

### Extension manifests
Defines the extension declaration schema and exposes its manifest types through the public SDK.

### `core/src/ufo/ext/manifest.py`

`data_model` · `startup and cross-cutting extension wiring`

This file is mostly a set of frozen data shapes. An extension does not directly poke at the running system to register itself. Instead, it returns a Manifest, which is like a signed order form saying: “I provide these tools, need these secrets, expose these web routes, run these jobs, add these prompt sections, and so on.” The core loader reads that order form during startup and wires each declared piece into the right part of the application.

A Pack is the same idea at a higher level. It names a group of extensions that should be activated together, plus any pack-level skills or onboarding steps. This lets a deployment select one coherent product setup instead of enabling many pieces by hand.

The file also defines the small records used inside a manifest. For example, CredentialSlot describes a secret an extension needs. InjectionTarget describes how a proxy can place that secret into an outgoing request without putting the raw secret inside the sandbox. JobSpec describes background work and which workspaces it should run for. RouteSpec describes HTTP endpoints. HookSpec describes points where an extension can observe or narrow behavior during a turn, such as before a tool call or after a user message arrives.

The important design idea is separation. Extensions declare capabilities; core decides when and how to activate them. Without this file, extensions would not have a common language for telling the system what they provide or require.


### `core/src/ufo/sdk/manifest.py`

`data_model` · `import time and extension development`

Extensions need to describe what they provide: tools, hooks, model providers, search providers, onboarding steps, prompt sections, and other pieces of a manifest. The real definitions live in `ufo.ext.manifest`, but this file deliberately re-exports them through `ufo.sdk.manifest`. Think of it like a front desk: visitors should ask here, not wander into the back office.

This matters because it creates a stable public contract. If extension code imported directly from internal locations, any internal reshuffle could break outside users. By importing through this file, extensions depend on the SDK-facing name, while the project can still reorganize internals later if needed.

There is no runtime logic here. The file simply imports each manifest type and exposes it under the same name. The repeated `as SameName` style makes the public exports explicit and clear to tools that inspect code. The opening comment also explains an architectural rule: `ufo.sdk` uses named modules like this one because package `__init__.py` files are kept empty. So this file is part of the project’s public API surface, not a place where manifest behavior is implemented.


### Handler runtime contracts
Provides the stable context and HTTP primitives extension handlers use while processing requests.

### `core/src/ufo/sdk/context.py`

`data_model` · `cross-cutting`

This file does not create new behavior. It gathers and re-exports context-related types from deeper parts of the project so extension authors have a simple, safe place to import them from. In everyday terms, it is like a reception desk: the real people work in different offices, but visitors only need to go to one front counter.

The context is the bundle of abilities and information given to an extension when it runs. It includes things like access to declared credentials, model access, scoped storage, page and source records, trajectory information, and references to agent changes or proposals. By exposing these through `ufo.sdk.context`, the project can keep the public API stable even if the internal files move or change shape later.

This matters because extensions should depend on the SDK surface, not on private implementation details. Without this file, extension code would need to import from modules such as `ufo.ext.context` directly, making it more fragile and harder for the core project to evolve without breaking users.


### `core/src/ufo/sdk/http.py`

`io_transport` · `request handling`

This file is the HTTP doorway for code that builds routes in the UFO SDK. Instead of asking extension authors to import web types directly from Starlette, the underlying web framework, it re-exports the request and response classes they are allowed to use. That keeps the public SDK surface small and stable: route code can say, in effect, “give me the project’s Request and Response types,” without caring where they come from underneath.

The file also protects session cookies. A cookie is a small value a browser stores and sends back on later requests. Session cookies are sensitive because they often prove who the user is. The helper function here, `set_session_cookie`, sets those cookies with fixed safety choices: `HttpOnly`, so browser JavaScript cannot read the cookie, and `Secure`, so it is only sent over HTTPS. It deliberately does not allow a cookie domain to be set. That makes the cookie “host-only,” meaning it belongs only to the exact host that issued it, not to a wider parent domain. This prevents a session from accidentally leaking across subdomains, preview sites, or deployment environments. Like a hotel key card that only opens one room, the cookie is not allowed to work in neighboring rooms.

#### Function details

##### `set_session_cookie`  (lines 17–25)

```
def set_session_cookie(response: Response, name: str, token: str, *, samesite: Literal['lax', 'strict', 'none']) -> None
```

**Purpose**: This function sets a session cookie on an HTTP response using the project’s required security settings. Code uses it when it needs to attach a login or session token to a response without risking weaker cookie settings.

**Data flow**: It receives a response object, a cookie name, a token value, and a chosen `SameSite` policy, which controls when browsers send the cookie during cross-site navigation. It passes those values to the response’s cookie-setting method, while always adding `HttpOnly` and `Secure` and never allowing a broader cookie domain. It returns nothing; the response object is changed so that it will tell the browser to store the cookie.

**Call relations**: When route or session-related code needs to bind a browser session, this helper is meant to be the safe path. Inside, it delegates the actual low-level cookie header creation to Starlette’s `Response.set_cookie`, but it wraps that call so callers cannot forget the required protections or widen the cookie to other hosts.

*Call graph*: 1 external calls (set_cookie).


### Capability SDK doorways
Re-exports public contracts for jobs, schedules, skills, surfaces, and tools without exposing internal module layout.

### `core/src/ufo/sdk/jobs.py`

`other` · `extension import and job declaration`

This file does not implement job behavior itself. Instead, it carefully re-exports a small set of names from deeper inside the core package, making them part of the supported extension-facing API. In everyday terms, it is like a labeled service counter: extension authors come here for the official job declaration tools, rather than wandering into the warehouse behind the counter.

The important idea is stability and safety. Background jobs need to say which workspaces have work waiting. They do that through `owner_candidates`, a helper for building a database query that returns workspace IDs. Core can then use those workspace IDs to decide where to run the job. The module also exposes `WorkspaceCandidates`, the type used for those candidate workspace queries, and `JobSpec`, the description of a background job in an extension manifest.

The long module comment explains an important design choice: candidate workspaces are computed each scheduler tick, not once forever. That means time-based jobs can calculate “what is due now” using the current time. Without this public re-export file, extensions would have to import from internal modules directly, making them more fragile when core code is reorganized.


### `core/src/ufo/sdk/scheduling.py`

`other` · `import time / cross-cutting public API`

This is a small public-facing wrapper file. The real scheduling code lives elsewhere, in `ufo.scheduling`, but outside users are meant to reach it through the SDK package. This file acts like a clearly labeled shelf in a shop: it does not manufacture anything itself, but it puts the scheduling tools where customers expect to find them.

It re-exports the schedule store, scheduled task value object, task inspection type, a constant for one-time schedules, and a helper for finding workspaces with due tasks. “Re-export” means it imports something from an internal module and exposes it again under this module’s name. That keeps the public API tidy without duplicating the actual implementation.

The comment at the top explains why this exists as a separate named module: the project does not allow code in `__init__.py` files, so public SDK pieces live in explicit modules such as this one. Without this file, extension authors would either have to import from the deeper internal scheduling module, which makes the public boundary less clear, or the SDK would be missing these scheduling names entirely.


### `core/src/ufo/sdk/skills.py`

`io_transport` · `cross-cutting`

This file is a small doorway into the project’s skill system. A “runtime skill” is a skill definition that an extension can contribute while the program is running, rather than something built into the core ahead of time. The actual code for that lives deeper in `ufo.skills.runtime`, but outsiders should not have to know that internal layout.

Instead, this module makes two names available through `ufo.sdk.skills`: `RuntimeSkill`, the value object that represents one skill, and `parse_skill_content`, the helper that reads in-memory skill text and turns it into that structured object. This is like a reception desk in a large building: it does not do the work itself, but it sends visitors to the right service without exposing the building’s back corridors.

The comment explains why this exists as a separate named module. The `ufo.sdk` package keeps its `__init__.py` empty because project rules forbid code there, so public SDK imports are grouped into explicit modules such as this one. Without this file, extension authors would need to import from internal paths, making their code more likely to break if the project reorganizes its internals.


### `core/src/ufo/sdk/surfaces.py`

`util` · `import time / extension development`

This file does not create new behavior. Instead, it re-exports names from deeper parts of the project under the public `ufo.sdk.surfaces` module. A “re-export” means it imports something from its real home and makes it available from this friendlier location too.

Its purpose is to give extension authors a stable, easy-to-find toolbox for building a surface. In this project, a surface is an integration point where outside code can receive conversation data, expose routes, request credentials, and write results back. Without this file, users would have to import pieces from many internal modules such as credentials, grants, transcript records, and surface internals. That would make extensions harder to write and more likely to break if the internal folder structure changes.

The file also reflects an important packaging rule in this codebase: `ufo.sdk` uses named modules rather than putting code in `__init__.py`. So `ufo.sdk.surfaces` acts like a labeled shelf in a hardware store: it does not manufacture the tools, but it puts all the tools for one job in the same place. The names exposed here include surface specifications and routes, context objects given to handlers, writeback support, shared artifacts, credential request types, transcript types, and user-interaction records.


### `core/src/ufo/sdk/tools.py`

`other` · `import time, when extensions load tool SDK names`

This file is a public doorway into the tool system. Instead of asking extension authors to import from deeper internal paths like `ufo.tools.context` or `ufo.tools.registry`, it re-exports the small set of names they are meant to use: tool definitions, tool contexts, tool results, text and image content, and the `ConnectUnavailable` error.

The idea is similar to a shop counter. The useful items may be stored in different rooms behind the scenes, but customers should come to one counter rather than wander through the warehouse. That makes the project easier to change later: internal files can move or be reorganized while the public import path, `ufo.sdk.tools`, stays the same.

There is no active logic here. The file does not create tools, run tools, or process results. It only imports selected classes and exposes them again under this SDK module. The comment also explains an important project rule: `ufo.sdk` uses named modules like this one for its public surface because code is not allowed in `__init__.py` files.
