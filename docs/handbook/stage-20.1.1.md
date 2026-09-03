# SDK web, browser, terminal, and surface seams  `stage-20.1.1`

This stage is a set of public doorways for people building on the SDK. It is not the main work loop itself. Instead, it sits at the edges of the system, where extension code talks to browsers, web pages, terminals, operator sessions, and user-facing “surfaces” without depending on hidden internal modules.

The browser module exposes the safe browser connection pieces, including the Chrome DevTools Protocol, a browser control channel used to attach to and reuse browser sessions. The callback page module creates the small confirmation page shown after sign-in, install, or consent steps. The HTTP module gives extensions standard request, response, upload, form, and cookie helpers, so route code does not need to touch the lower-level web framework. The operator module re-exports web session tools meant only for operator use. The surface token module exposes stable helpers for creating and checking link-like surface addresses. The surfaces module gathers the main surface extension types and helpers. The terminal module does the same for terminal transport, errors, timing, and blob storage.

## Files in this stage

### Web and browser seams
Public SDK entry points for browser transport, callback completion pages, and HTTP request/response helpers.

### `core/src/ufo/sdk/browser.py`

`io_transport` · `cross-cutting, especially during per-turn browser connection and reattachment`

This file is intentionally small, but it matters because it defines a clean public boundary. Extensions need a way to provide or reconnect to a browser, but they should not depend directly on UFO’s internal browser implementation. This module acts like a front desk: it does not do the browser work itself, but it tells extension authors, “these are the browser-related names you may use.”

The browser connection here is based on CDP, the Chrome DevTools Protocol, which is the remote-control interface Chrome exposes for automation. A provider creates a short-lived lease for a browser session. That lease gives the engine a connection endpoint, including the URL and any needed headers. At the end of a turn, the lease can be released. If work must resume later, a saved token can be used to reattach, unless the old session is gone.

The file also exposes helper concepts for placing files where the browser can open them, and for letting the host help rank found page elements. All concrete behavior lives in `ufo.browser`; this file simply re-exports the approved public names under `ufo.sdk.browser` so extensions can rely on a stable SDK surface.


### `core/src/ufo/sdk/callback_page.py`

`io_transport` · `request handling`

This file exists for a very specific moment: a person leaves a chat, command line, or other tool to finish something in a browser, such as approving access with another provider. When that provider sends the browser back, there may be no normal app session attached to the page. The page can only say what just happened and what the person should do next.

The file defines a tiny HTML page template with built-in styling. It deliberately avoids heavy assets like fonts or a full stylesheet, because this page is meant to load quickly for a one-time confirmation. The only image it fetches is the product logo from the same server. The page also respects the browser’s light or dark theme.

There are a few standard messages for common endings, such as “Close this tab” or “Close this tab and return to the conversation.” If the caller provides a link, the page shows a button-like link back to the conversation. If the caller asks it to close automatically, the page adds a small script that tries to close the browser tab after a short delay. Browsers often block that unless the tab was opened by script, so the link is the reliable fallback when the user needs to go somewhere.

#### Function details

##### `callback_page`  (lines 70–90)

```
def callback_page(*, headline: str, detail: str='', link: PageLink | None=None, status: int=200, close: bool=False) -> HTMLResponse
```

**Purpose**: Builds the finished HTML response for a callback page. Callers use it when they need to show a short browser message after an external provider sends the user back.

**Data flow**: It receives a headline, an optional detail line, an optional return link, an HTTP status code, and a flag saying whether the page should try to close itself. It safely escapes user-visible text so special characters cannot turn into unwanted HTML, fills those pieces into the page template, optionally adds link styling and the close-tab script, and returns an HTMLResponse with the chosen status code.

**Call relations**: This is the single page-building step used by callback flows that need to answer a browser return. Inside, it calls html.escape to make the supplied text safe for HTML, then hands the completed page string to ufo.sdk.http.HTMLResponse so the web layer can send it back to the browser.

*Call graph*: 2 external calls (escape, HTMLResponse).


### `core/src/ufo/sdk/http.py`

`io_transport` · `request handling`

This file is the public HTTP doorway for UFO extensions. A route handler needs to receive a web request and send back a web response, but the project does not want every extension to import those pieces from Starlette, the underlying web framework, on its own. Instead, this file re-exports the approved request, response, upload, and form-error classes in one place.

It also centralizes an important security rule: how UFO sets session cookies. Cookies are small pieces of data a browser stores and sends back later, often to prove the user is logged in. If cookies are set carelessly, they can leak across subdomains or be sent in unsafe situations. The helper here makes UFO cookies host-only by never setting a cookie domain, marks them as HttpOnly so browser scripts cannot read them, and lets callers choose only the allowed SameSite and Secure behavior. SameSite controls when browsers send cookies during cross-site navigation; Secure means the cookie should only travel over HTTPS.

The two smaller helpers decide when plain HTTP is acceptable and when a cookie may be marked Secure. The important idea is that the published public address, not the internal request URL, is the source of truth. This matters when TLS/HTTPS is terminated by a front proxy before traffic reaches the app.

#### Function details

##### `cookie_secure`  (lines 24–33)

```
def cookie_secure(published_scheme: str) -> bool
```

**Purpose**: Decides whether session cookies should use the Secure flag based on the public scheme the site says it is using. In plain terms: if the site is published as anything other than plain HTTP, the cookie should be treated as HTTPS-only.

**Data flow**: It receives a scheme string such as "http" or "https". It compares that value to "http". It returns false only for plain HTTP, and true for everything else, including HTTPS or an unspecified/non-HTTP scheme.

**Call relations**: Other code should call this before setting a session cookie, then pass the result into set_session_cookie. The key point is that callers should use the published public scheme, not the scheme on the incoming internal request, because a proxy may have already converted HTTPS traffic into plain HTTP before it reaches the app.


##### `plain_local`  (lines 36–43)

```
def plain_local(published_base: str | None) -> bool
```

**Purpose**: Checks whether a published base URL is a plain HTTP address that stays on the local machine, such as localhost or a subdomain ending in .localhost. This identifies the one kind of non-HTTPS publishing that is treated as acceptable for local development.

**Data flow**: It receives a published base URL, or no value. It parses that URL with urlsplit so it can inspect the scheme and host separately. It returns true only when the scheme is "http" and the host is exactly "localhost" or ends with ".localhost"; otherwise it returns false.

**Call relations**: This helper is used as a small policy check around published addresses. It hands URL parsing to urllib.parse.urlsplit, then gives the rest of the system a simple yes-or-no answer about whether the base URL is a local plain-HTTP development address.

*Call graph*: 1 external calls (urlsplit).


##### `set_session_cookie`  (lines 46–65)

```
def set_session_cookie(response: Response, name: str, token: str, *, samesite: Literal['lax', 'strict', 'none'], secure: bool) -> None
```

**Purpose**: Sets a UFO session cookie in the one approved, safe way. It makes the cookie host-only, unreadable by browser JavaScript, and controlled by explicit SameSite and Secure settings.

**Data flow**: It receives a response object, the cookie name, the session token value, and two security choices: SameSite and Secure. It calls the response's set_cookie method with those values, always adding httponly=True and never passing a domain. The response is changed in place so that, when sent to the browser, it includes the Set-Cookie header.

**Call relations**: Route or authentication code calls this when it needs to bind a browser session to a response. It delegates the actual header creation to Starlette's Response.set_cookie, but wraps that call so project-wide cookie rules cannot be skipped accidentally. Callers are expected to compute the secure value with cookie_secure before using this function.

*Call graph*: 1 external calls (set_cookie).


### Operator and surface APIs
Stable public imports for operator web sessions, durable surface tokens, and surface extension building blocks.

### `core/src/ufo/sdk/operator.py`

`other` · `cross-cutting`

This file is a small public doorway into the project’s operator web-session support. An “operator” here means a privileged user or surface, such as a debug or administration tool, that should only be available in the operator domain. Rather than making callers know the deeper internal path where this logic lives, the SDK exposes the important names here.

The file re-exports three things. `resolve_operator_workspace` is the helper used to identify which workspace an operator request is trying to reach, including support for a `?ws=` query parameter. `bind_operator_session` is the POST-side helper that connects an operator request to the shared session cookie, so the browser can keep using the same authenticated session. `FleetDirectory` is the shared directory that operator-facing surfaces use to register or find the fleet/workspace information they expose.

The important behavior is that this file is intentionally thin. It is like a labeled front desk: it points callers to the right service without doing the service itself. That matters because SDK imports can stay stable even if the internal runtime package layout changes later.


### `core/src/ufo/sdk/surface_token.py`

`io_transport` · `cross-cutting`

This file is a small public doorway into deeper authentication code. In this project, a “surface” needs a way to mint and verify its own permanent link addresses without directly holding the deployment’s secret token material. That separation matters because secrets should stay in the trusted authentication layer, not spread into every public-facing SDK module.

The file re-exports two functions from `ufo.harness.auth.surface_token`: one for minting a surface token and one for verifying it. A re-export means “make this existing function available here too.” It is like putting a clearly labeled customer entrance on a building, even though the actual machinery is inside another room.

The comment explains an important project rule: `ufo.sdk` keeps its package initializer empty, so public SDK features are exposed through named modules like this one instead of through `__init__.py`. That keeps imports predictable and avoids hidden startup behavior. Without this file, outside users would need to know the internal harness path to use surface token features, which would make the SDK harder to learn and more fragile if internals move later.


### `core/src/ufo/sdk/surfaces.py`

`other` · `cross-cutting import-time public API`

A surface is an outside-facing way for UFO to talk to people or systems, such as a chat interface, inbox, or other integration point. Extension authors need many shared types to describe their surface, receive privileged context, request credentials, work with conversations, and write results back. Without this file, those authors would have to import from many internal paths, which would make their code harder to read and more likely to break if the project reorganizes its internals.

This module does not define new behavior. It is a curated re-export file: it imports names from internal UFO packages and immediately exposes them under `ufo.sdk.surfaces`. Think of it like a front desk directory. The real offices are elsewhere, but visitors get one clear place to ask for what they need.

The exports cover several groups: surface registration types such as `SurfaceSpec` and `SurfaceRoute`; context objects such as `SurfaceContext`; writeback and shared artifact types; conversation and transcript records; credential and connection request errors; terminal and workspace objects; and small helper functions for formatting or recognizing special surface messages. The comment at the top also explains why this lives in a named module rather than in `ufo.sdk.__init__`: project rules do not allow code in package `__init__.py` files.


### Terminal transport seam
Public SDK access to terminal transport types, errors, timing constants, and blob storage support.

### `core/src/ufo/sdk/terminal.py`

`io_transport` · `cross-cutting`

This module does not build new terminal behavior itself. Instead, it gathers together the pieces that a terminal-transport extension is expected to use and re-exports them under the public `ufo.sdk.terminal` name. In plain terms, it is like a labeled shelf at the front of a workshop: the actual tools live elsewhere, but this shelf tells extension authors which tools are safe and intended to use.

The file exposes blob storage types from `ufo.blob`, including `BlobStore`, which is the fleet-wide place for storing and retrieving binary data, and `BlobNotFound`, the error used when requested stored data is missing. It also exposes terminal sandbox types from `ufo.harness.sandbox.terminal`, such as `TerminalTransport`, the interface a transport extension implements, and `Terminals`, the in-process backend it may reuse. The exported errors, such as `TerminalAbsent`, `TerminalGone`, and `TerminalOpFailed`, give callers standard ways to describe common terminal failures.

The reason this file matters is stability and clarity. The project avoids putting public SDK code in package `__init__.py` files, so named modules like this one become the official import locations. Without this file, extension authors would need to know the internal module layout and import directly from deeper implementation paths, making their code more fragile if the project is reorganized.
