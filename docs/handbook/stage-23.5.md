# Public SDK Web, Surface, Sandbox, and Utility Facades  `stage-23.5`

This stage is shared support for extension authors. It is the public “front counter” of the SDK: small modules that give stable, approved import paths, while hiding the deeper internal file layout. Most files do not create new behavior. They re-publish tools that already exist so extension code does not depend on parts that may move later.

The utility facades cover common needs. audience exposes conversation audience names and helpers. flags exposes the feature-flag check used to turn behavior on or off. o11y opens the approved logging and metrics doorway. scheduled_fire exposes helpers for making and reading scheduled-task keys. listings and objects provide public types and constants for portal lists and object features. sandbox gathers sandbox types and helpers. operator exposes shared operator-only web session tools. surfaces gathers the building blocks for UI surface extensions.

The web-facing pieces support browser interactions. http provides request and response types plus safe session-cookie handling. callback_page builds the small page users see after sign-in, install, or consent, guiding them back or telling them they may close the tab.

## Files in this stage

### Cross-Cutting Utilities
Public SDK facades for audience helpers, feature flags, observability, and scheduled-task key utilities.

### `core/src/ufo/sdk/audience.py`

`util` · `cross-cutting`

This file is a small public doorway into the project’s audience system. In this project, an “audience” means who a message or turn is meant for: everyone in a shared space, a specific room, a foreign room, or particular audience members. The real definitions and logic live deeper inside `ufo.turns.audience`, but outside users should not have to know that internal path.

Instead, this file imports the useful pieces from the internal module and exposes them under `ufo.sdk.audience`. That makes the SDK easier and safer to use. It is like putting the most-used tools at the front desk instead of asking visitors to search the storage room.

The exported items include the `Audience` value, constants such as `SHARED_AUDIENCE` and `FOREIGN_AUDIENCE_PREFIX`, and helper functions for building, parsing, and displaying audiences. If this file disappeared, existing SDK users who import audience helpers from the public SDK path would break, even though the underlying audience logic still exists elsewhere. Its main value is stability: it separates the public API people rely on from the internal layout of the codebase.


### `core/src/ufo/sdk/flags.py`

`util` · `cross-cutting`

This file is a small public doorway. Elsewhere in the project, `ufo.flags` contains the real logic for checking whether a feature flag is turned on. A feature flag is a switch that can enable or disable a piece of behavior without changing the calling code. This file re-exports that checker as `ufo.sdk.flags.flag_enabled`, so extensions and SDK users can depend on a stable, intended import path instead of reaching into an internal module.

The practical problem it solves is API cleanliness. Without this file, outside code might import `flag_enabled` from wherever it happens to live today. If the internal layout later changes, those imports could break. By providing this thin SDK layer, the project can keep the public promise in one place while preserving freedom to reorganize the internals.

There is no extra behavior here: no new decision-making, no storage, and no side effects beyond importing the existing function. Think of it like a signposted front desk that points to the same service already available inside the building.


### `core/src/ufo/sdk/o11y.py`

`util` · `cross-cutting`

This file is a small bridge between extension code and the project’s built-in observability system. Observability means the tools that help people see what the software is doing while it runs, such as structured logs and metrics counters. Structured logs are log messages with consistent fields, so machines and humans can search them reliably. Metrics are counted measurements, such as how often something happens.

The important idea here is control. Extensions are allowed to report events, warnings, metrics, and profiling information, but they do so through functions that live in the core system. The comment explains why: the list of valid counters stays centralized. An extension can emit a metric name that core already knows about, or it fails loudly. That prevents a growing fleet of plugins from creating random, inconsistent metric names that are hard to track.

In practice, this file does not implement logging or metrics itself. It re-exports four functions from `ufo.o11y`: `emit_metric`, `log`, `turn_profile`, and `warn`. Think of it like a clearly labeled service window: extensions come here to send observability information, while the real machinery stays behind the wall in core.


### `core/src/ufo/sdk/scheduled_fire.py`

`util` · `cross-cutting`

This is a small public doorway into scheduling support. In this project, a “scheduled fire” is a run triggered by a schedule, much like a cron job. Each such run needs a consistent key so the system can tell which scheduled task it belongs to later. This file does not create that key itself. Instead, it re-exports two helpers from the internal extension module: one helper builds the key, and the other reads the task ID back out of it.

The reason this file matters is stability. Code outside this package can import these helpers from `ufo.sdk.scheduled_fire`, which is a public-facing location. If the internal implementation moves, callers do not have to change as long as this SDK file keeps pointing to the right place. It is like a front desk: visitors ask here, and the front desk routes them to the correct back-office function.

There are no local functions or classes here. Its whole job is to make `scheduled_fire_key` and `scheduled_fire_task_id` available under a clean, documented SDK name.


### Web Transport Flows
SDK-facing helpers for browser callback pages and HTTP route request, response, and cookie handling.

### `core/src/ufo/sdk/callback_page.py`

`io_transport` · `request handling`

When someone connects an outside service, their browser often ends up on a final callback page. That page may not know anything about the original Slack thread, command-line session, or chat where the action began. It only knows the result of the return trip from the outside provider. This file creates that final page in a safe, tiny, predictable way.

The page is deliberately simple: a logo, a headline, an optional detail line, an optional link back to the conversation, and optionally a small script that tries to close the browser tab after a short delay. The close attempt is only a convenience, because browsers usually allow a page to close itself only if script opened the tab in the first place. The link is the reliable fallback: it gives the user a clear way back.

The file also keeps the page lightweight. It avoids loading a stylesheet or font, uses the browser’s light or dark theme automatically, and fetches only the project logo. Before putting text or link values into the HTML, it escapes them, meaning special characters are made harmless so user-facing content cannot accidentally become browser code.

#### Function details

##### `callback_page`  (lines 70–90)

```
def callback_page(*, headline: str, detail: str='', link: PageLink | None=None, status: int=200, close: bool=False) -> HTMLResponse
```

**Purpose**: Builds the final HTML response shown in the browser after a provider callback or install flow. Callers use it to show a short outcome message, optionally include a return link, optionally try to close the tab, and set the HTTP status code.

**Data flow**: It receives a headline, an optional detail message, an optional PageLink with button text and a URL, an HTTP status number, and a flag saying whether the page should try to close itself. It escapes the visible text and URL so they are safe to place inside HTML, fills those values into the page template, adds link styling only when a link exists, adds the close script only when requested, and returns an HTMLResponse containing the finished page and status code.

**Call relations**: This is the file’s page-making entry point for callback responses. Other return-leg code calls it when a browser needs a final answer. Inside, it relies on html.escape to make inserted text safe, then hands the finished HTML string to ufo.sdk.http.HTMLResponse so it can be sent back as a proper web response.

*Call graph*: 2 external calls (escape, HTMLResponse).


### `core/src/ufo/sdk/http.py`

`io_transport` · `request handling`

This file acts like a front desk for HTTP-related tools. Instead of extension code importing directly from Starlette, the web framework underneath, it imports from `ufo.sdk.http`. That keeps UFO’s public interface small and stable: route handlers receive a `Request` and return a `Response`, and they can build common replies such as HTML, JSON, plain text, redirects, or streaming responses from the names re-exported here.

The file also protects an important security rule for session cookies. A cookie is a small piece of data a browser stores and sends back on later requests. Session cookies are especially sensitive because they can prove who a user is. `set_session_cookie` is the approved helper for creating them. It always makes the cookie `HttpOnly`, meaning browser JavaScript cannot read it, and it deliberately does not allow a `domain` setting. That makes the cookie host-only, so it cannot accidentally spread across subdomains, preview hosts, or other environments.

The companion helper, `cookie_secure`, decides whether the browser should only send the cookie over HTTPS. It bases that choice on the site’s published scheme, not the incoming request, because many deployments receive plain HTTP internally after HTTPS has already been handled by a front door proxy.

#### Function details

##### `cookie_secure`  (lines 23–32)

```
def cookie_secure(published_scheme: str) -> bool
```

**Purpose**: This function decides whether a session cookie should be marked `Secure`, which tells the browser to send it only over HTTPS. It uses the public scheme the site is published under, because internal requests may look like plain HTTP even when users are visiting an HTTPS site.

**Data flow**: It takes one input: `published_scheme`, such as `http` or `https`. If the scheme is exactly `http`, it returns `False`, because a browser would drop a `Secure` cookie from a plain-HTTP origin. For every other scheme, it returns `True`, keeping the safer default.

**Call relations**: Other code that needs to create a session cookie uses this decision before calling `set_session_cookie`. Together, the two functions make sure cookies are both usable in plain-HTTP deployments and protected in HTTPS deployments.


##### `set_session_cookie`  (lines 35–54)

```
def set_session_cookie(response: Response, name: str, token: str, *, samesite: Literal['lax', 'strict', 'none'], secure: bool) -> None
```

**Purpose**: This is the approved way to attach a UFO session cookie to an HTTP response. It enforces safety rules so callers cannot accidentally create a cookie that JavaScript can read or that leaks across related hostnames.

**Data flow**: It receives a response object, a cookie name, the session token to store, a `SameSite` setting, and a `secure` choice. It then adds a `Set-Cookie` header to the response by calling the underlying response’s cookie-setting method. The response is changed in place; the function returns nothing.

**Call relations**: Route or request-handling code calls this when it has decided to bind a session to the browser. It hands the final low-level work to `starlette.responses.Response.set_cookie`, but keeps control over the important options: `HttpOnly` is always turned on, `Secure` is supplied from the caller, and no cookie domain is allowed.

*Call graph*: 1 external calls (set_cookie).


### Resource Facades
Stable import points for listing, object, and sandbox types and helpers used by extension authors.

### `core/src/ufo/sdk/listings.py`

`other` · `cross-cutting`

Portal listings can be long, so the system returns them in pages instead of all at once. This file exposes the public names used for that paging flow: cursors, pages, cursor errors, and helper functions for building or reading paged results. A cursor is a small marker that says “continue from here” when asking for the next page, much like a bookmark in a book.

The file does not define new behavior itself. Instead, it re-exports selected items from `ufo.listings` under the SDK namespace, `ufo.sdk.listings`. That matters because extensions should depend on the stable public SDK surface, not on internal module locations that might change later. If this file were missing, extension code would either fail to import these listing tools or would need to reach into less-public parts of the project.

The repeated `as` imports are intentional: they make these imported names explicit members of this module. In plain terms, this file says, “these are the listing tools extension authors are allowed and expected to use.”


### `core/src/ufo/sdk/objects.py`

`other` · `import time / extension development`

This module does not define new behavior. Instead, it gathers many object-related names from inside the project and re-exports them from one stable place: `ufo.sdk.objects`. In everyday terms, it is like a front desk for extension authors. Rather than wandering through private back rooms of the codebase to find object kinds, object references, store interfaces, ownership helpers, and error types, outside code can import them from this single public counter.

That matters because extensions need to register object kinds and write object stores without being tightly tied to the project’s internal layout. If internal files move or get reorganized, the SDK module can keep presenting the same public names. Without this file, extension authors would likely import from internal modules directly, making their code more fragile.

The file also reflects a project rule: package `__init__.py` files stay empty, so public SDK surfaces live in named modules like this one. The repeated `as Name` imports are intentional. They make the exported public names explicit and clear to readers and tools.


### `core/src/ufo/sdk/sandbox.py`

`other` · `import time / SDK boundary`

This module does not create new sandbox behavior. Instead, it gathers the important sandbox pieces from deeper inside the codebase and re-exports them under `ufo.sdk.sandbox`, which is the public path meant for outside users. Think of it like a reception desk: the real offices are elsewhere, but visitors only need to know one address.

The sandbox is the isolated place where commands, files, skills, and browser-related tools can run without freely touching the host system. Extension authors need shared names for things like `SandboxSession`, `SandboxSpec`, `Carrier`, path helpers, containment checks, proxy settings, and sandbox environment constants. This file makes those names available as the supported SDK surface.

That matters because internal modules can move or change over time. If outside extensions imported directly from `ufo.sandbox.session` or other internal locations, those extensions would be fragile. By re-exporting here, the project can keep a clearer boundary between “public contract” and “internal implementation.”

A comment at the top also explains an important project rule: `ufo.sdk` uses named modules like this one because package `__init__.py` files are kept empty. So this file is intentionally a thin public wrapper, not a place for logic.


### Extension Surfaces and Sessions
Public facades for operator-only sessions and surface-extension authoring tools.

### `core/src/ufo/sdk/operator.py`

`other` · `cross-cutting`

This file is a small public doorway. Operator-only pages, such as debug or fleet administration tools, need a common way to recognize the current operator workspace, bind the operator session cookie, and look up registered operator-facing surfaces. Rather than making every caller know the deeper internal path where that logic lives, this module exposes the same pieces under `ufo.sdk.operator`.

The three exported names come from `ufo.ext.operator`. `resolve_operator_workspace` is the resolver that checks whether a request belongs to the operator domain and reads the `?ws=` workspace selector. `bind_operator_session` is the POST-side helper that attaches the shared session cookie after the operator flow accepts it. `FleetDirectory` is the directory that lets operator tools register and find fleet-facing surfaces.

The practical value is stability and clarity. If the internal implementation moves, code that imports from the SDK path can keep working. Without this file, every operator-only tool would either depend directly on internal extension paths or duplicate import choices, making the system harder to change safely.


### `core/src/ufo/sdk/surfaces.py`

`other` · `import time / extension development`

A “surface” is an extension point where outside code can connect UFO to some user-facing place, such as a chat app, inbox, or other interface. This file does not create new behavior itself. Instead, it re-exports the important pieces from deeper internal modules so extension authors can import them from `ufo.sdk.surfaces` instead of needing to know the project’s internal folder layout.

That matters because public extension code should not depend on where every internal class lives. If the project later moves `SurfaceSpec`, `SurfaceContext`, `Writeback`, credential types, transcript records, terminal frame types, or workspace-file types to different internal files, this module can keep the public import path steady. It is like a reception desk: visitors do not need to know which office each person sits in; they go to one known place and are directed to the right things.

The file also reflects a project rule mentioned in the comment: `ufo.sdk` uses named modules rather than putting code in `__init__.py` files. So this file is the named public module for surface-related SDK imports. Without it, extension authors would have to import from many internal packages, making their code more fragile and harder to understand.
