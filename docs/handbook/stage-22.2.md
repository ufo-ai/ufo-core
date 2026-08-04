# SDK Extension Runtime and Declaration Facades  `stage-22.2`

This stage is shared support for people writing UFO extensions. It is not the main work loop itself. Instead, it is the public front door that extension code uses while the system runs. These files act like a reception desk: they offer stable names and hide the private room layout behind them, so outside code does not break when internals move.

The context doorway gives extensions safe access to facts about the current run, such as identity, credentials, pages, sources, and recorded steps. The HTTP toolkit provides request and response objects, file uploads, forms, and cookies for extension web routes without exposing the underlying web library. The jobs and scheduling doorways expose the approved ways to declare background work and when it should run. The manifest file supplies the public types used to describe an extension. The skills file exposes skill objects and the parser that reads skill declarations. The tools file provides the public classes for declaring callable tools. The observability file lets extensions log events and report metrics using names controlled by the core system.

## Files in this stage

### Runtime context facade
Stable imports for extension code that needs information about the current UFO run, identity, credentials, pages, sources, and trajectories.

### `core/src/ufo/sdk/context.py`

`other` · `cross-cutting; used whenever extension code imports SDK context helpers`

This file does not create new behavior. Instead, it acts like a well-labeled front desk for the SDK, which is the set of tools exposed to people writing extensions. Without this file, extension code would need to know the project’s internal module layout and import context pieces from many different places. That would make extensions harder to write and more likely to break when internal files move.

The file re-exports names from deeper modules. A re-export means: “make this thing available here too.” For example, extension handlers can import `ExtensionContext`, `CredentialAccess`, `ModelAccess`, `PageState`, or `Trajectory` from `ufo.sdk.context` instead of hunting through internal packages. It also exposes identity-related helpers like `agent_current`, credential error types, source-reading records, surface installation access, and schema records such as `AgentChange` and `ProposalRef`.

The important idea is stability. Internal modules can be organized for the project’s own needs, while this file presents a cleaner public surface for outsiders. It is like a hotel concierge: the services live elsewhere, but guests only need one desk to ask for them.


### HTTP route facade
Public request, response, upload, form, and cookie types for extensions that expose HTTP routes.

### `core/src/ufo/sdk/http.py`

`io_transport` · `request handling`

This file acts like a small front desk for HTTP-related pieces used by UFO route handlers. A route handler is the code that receives a web request and returns a web response. Instead of asking extension authors to know which outside library provides each HTTP class, this module re-exports the approved types from one stable place: `ufo.sdk.http`.

The imported types cover common web tasks. `Request` is the incoming browser or API request. `Response` and its concrete forms, such as `HTMLResponse`, `JSONResponse`, `PlainTextResponse`, `RedirectResponse`, and `StreamingResponse`, are the different ways a route can answer. `UploadFile` and `FormData` are used when a browser submits a form, especially one with file uploads. `FormParserError` is exposed so routes can catch a badly formed submitted form and return a clear client error instead of crashing.

The file also contains one important safety helper: `set_session_cookie`. Cookies are small pieces of data stored by the browser. Session cookies are sensitive because they can prove a user is logged in. This helper sets them in a deliberately narrow and secure way: no shared parent domain, always `HttpOnly` so page scripts cannot read them, and always `Secure` so they are sent only over HTTPS. In short, this file gives extensions a safe, consistent HTTP vocabulary.

#### Function details

##### `set_session_cookie`  (lines 23–34)

```
def set_session_cookie(response: Response, name: str, token: str, *, samesite: Literal['lax', 'strict', 'none']) -> None
```

**Purpose**: This function is the approved way to attach a session cookie to an HTTP response. It makes the cookie host-only and forces important browser protections, so session data cannot accidentally spread across subdomains or be read by client-side scripts.

**Data flow**: It takes a response object, a cookie name, a token value, and a `SameSite` choice, which tells browsers when to send the cookie during cross-site navigation. It then writes one `Set-Cookie` instruction onto the response with `HttpOnly` and `Secure` always turned on, and with no domain set. Nothing is returned; the response is changed in place so the browser will receive the cookie.

**Call relations**: When route or session code needs to bind a session to the browser, it should call this helper instead of setting the cookie by hand. The helper then hands the actual low-level work to Starlette’s `Response.set_cookie`, but only after fixing the security choices that UFO requires.

*Call graph*: 1 external calls (set_cookie).


### Background execution facades
Stable SDK entry points for declaring and scheduling background work without depending on internal modules.

### `core/src/ufo/sdk/jobs.py`

`other` · `extension declaration and scheduler setup`

This is a small “front door” module for background job declarations. Instead of asking extension authors to import from deeper internal paths, the project gives them a stable public path: `ufo.sdk.jobs`. That matters because internal code can move around later without breaking extensions, as long as this public doorway keeps exporting the same names.

The file re-exports three things. `JobSpec` is the description of a background job an extension wants core to run. `WorkspaceCandidates` and `owner_candidates` help a job say which workspaces may have work waiting. A workspace is a separate area of data, and jobs should only run where there is something due to do.

The comment explains an important pattern: an extension provides a builder that creates a database query each time the scheduler ticks. That query selects distinct workspace IDs from the extension’s own tables. Building it each tick lets the query use the current time, so jobs can be due “now” rather than based on an old cutoff. Core then uses a special safe read that bypasses row-level security, meaning normal per-user data filtering, only for the purpose of finding which workspaces to bind the job to.


### `core/src/ufo/sdk/scheduling.py`

`other` · `cross-cutting import time`

This module does not define new behavior. Its job is to present a clean, stable public face for scheduling. In this project, the `ufo.sdk` package avoids putting code in `__init__.py` files, so each part of the public SDK lives in a named module like this one.

Think of it like a labeled shelf in a workshop. The actual tools are stored elsewhere, but this shelf gathers the scheduling tools that outside extension code is meant to pick up. It re-exports the schedule store that extensions use through their extension context, the value object returned for scheduled tasks, inspection information, a constant for one-time schedules, and a helper for finding workspaces with tasks that are due.

Without this file, extension authors would either have no convenient public import path for scheduling, or they would need to import directly from `ufo.scheduling`. That would make the internal layout harder to change later, because outside code would depend on it. By keeping this thin re-export layer, the project can offer a friendly SDK path while preserving freedom to reorganize the internals.


### Extension declaration facades
Public declaration surfaces for manifests, skills, and tools used by extension authors.

### `core/src/ufo/sdk/manifest.py`

`data_model` · `cross-cutting; active when extension code imports manifest types`

This file exists to keep the extension-facing API clean and stable. A manifest is the description of what an extension provides: things like skills, hooks, credential needs, search providers, routes, onboarding steps, and prompt sections. Those types are actually defined elsewhere, mostly in `ufo.ext.manifest` and a couple in `ufo.credentials`, but extension authors are not expected to know or depend on those internal locations.

Think of this file like a reception desk. The real offices are deeper inside the building, but visitors go to one obvious desk and ask for what they need. If the internal layout changes later, the project can update this file while keeping the outside import path the same.

There is no runtime logic here. It does not create manifests, validate them, or transform data. It simply re-exports names under the `ufo.sdk.manifest` module. The comments also explain an important project rule: `ufo.sdk` keeps its package initializer empty, so public SDK names live in explicit modules like this one rather than in `__init__.py`. Without this file, extension code would have to import from internal modules, making extensions more fragile when the core project is reorganized.


### `core/src/ufo/sdk/skills.py`

`other` · `cross-cutting import time`

This module is a small doorway into the project’s skill system. A “skill” here means a runtime capability that an extension can declare and contribute. Instead of asking outside code to import from deeper internal paths, this file exposes the two public names that extension code is expected to use: `RuntimeSkill`, the value object that represents a skill, and `parse_skill_content`, the function that reads skill content into that form.

The important reason this file exists is stability. Internal folders can move or be reorganized, but code outside the project should not have to change every time that happens. This module acts like a front desk: callers come to `ufo.sdk.skills`, and the front desk points them to the real implementation in `ufo.skills.runtime`.

There is no extra logic here. Importing this file just imports and republishes those two names. The comment also explains a project rule: the package’s `__init__.py` files are kept empty, so public SDK features live in named modules like this one rather than being gathered at package initialization time.


### `core/src/ufo/sdk/tools.py`

`io_transport` · `cross-cutting`

This file is a small public doorway into the project’s tool system. Extensions need to describe tools, receive tool input, return tool output, and sometimes report that a required connection is not available. The real implementations live deeper inside the codebase, but extension authors should not have to know those internal paths.

Think of it like a reception desk in a large building. Visitors do not need to know which office holds each form; they go to the desk and get the approved version. Here, the “desk” is `ufo.sdk.tools`. It re-exports selected names such as `ToolContext`, `ToolResult`, `TextContent`, `ImageContent`, `ToolDef`, and `ConnectUnavailable`.

The comment at the top explains an important project rule: package `__init__.py` files are kept empty, so public imports are placed in named modules like this one instead. Without this file, extension code would either import from internal modules directly, making it more fragile when internals move, or lack a clear supported import path.


### Observability facade
Small public logging and metrics doorway for extension code while keeping core metric names controlled.

### `core/src/ufo/sdk/o11y.py`

`util` · `cross-cutting`

Extensions often need to leave breadcrumbs: a log message for humans, a warning when something looks wrong, or a metric counter so operators can see how often something happens. This file is the safe, public-facing entry point for that. It does not create its own logging or metrics system. Instead, it re-exports three approved tools from the core observability module: `log`, `warn`, and `emit_metric`.

The important idea is control. Metrics are like labels on a factory dashboard. If every extension could invent new labels freely, the dashboard would become messy and hard to trust. The comment explains that metric names must already be declared in core. If an extension tries to emit a metric name core does not know about, it should fail loudly instead of silently adding a new, unexpected measurement.

So this file matters because it keeps extension authors on a clear, supported path. They can observe their behavior, but the overall project still has one enumerable, predictable metric surface. Without this small wrapper, extensions might import private internals directly or invent inconsistent reporting patterns.
