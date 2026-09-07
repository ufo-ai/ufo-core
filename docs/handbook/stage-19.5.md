# Public SDK package utilities and lightweight helpers  `stage-19.5`

This stage is shared behind-the-scenes support for people building on the public UFO SDK. It is not the main work loop itself. Instead, it provides stable “front doors” that extension code can import, even when the deeper internal code moves around.

The package marker makes ufo.sdk importable. Several files are simple signposts: delivery_register re-exports prompt text used by the runtime, flags re-exports feature-flag helpers, scheduled_fire exposes helpers for making and reading keys for scheduled task runs, and o11y opens the approved path for logging and metrics, meaning records of what happened and simple measurements.

Other files provide small pieces of active support. callback_page builds the browser page shown after sign-in, install, or consent, guiding the person back, closing the window, or redirecting them. http gathers safe request and response types and centralizes session cookie rules. untrusted gives everyone the same wrapper for text that came from outside the system, so it can be displayed with the right caution. Together, these helpers make extensions safer and more consistent.

## Files in this stage

### SDK package surface
Stable package and re-export modules define the public import surface for SDK users.

### `core/src/ufo/sdk/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means other parts of the project can write imports that start with `ufo.sdk`, rather than referring to files by their physical location on disk. Think of it like putting a label on a folder in a filing cabinet: the label does not contain documents itself, but it tells the system that the folder is a named place where related documents belong. Because this file is empty, it does not set up configuration, expose shortcuts, or run startup code. Its value is structural: without it, some Python environments or tooling might not recognize `core/src/ufo/sdk` as a package, and imports from this SDK area could fail or behave differently.


### `core/src/ufo/sdk/delivery_register.py`

`other` · `cross-cutting prompt construction`

This module is like a clearly labeled front desk for two prompt-building constants that live deeper inside the system. The project keeps `ufo.sdk` as a public software development kit, meaning code outside the core system can import from it without needing to know the internal folder layout. Because this project does not allow executable code inside `__init__.py` package files, each public item gets its own small named module like this one.

The constants re-exported here describe the “delivery register,” which is a shared section added to prompts so that direct model calls, the shell, and subagent prompts all write results in the same expected place. In plain terms, it helps every worker put their finished work in the same mailbox. Without this shared import point, extensions might reach into internal runtime paths directly, making them more fragile if the internal layout changes.

There are no functions or classes here. The file’s job is to preserve a clean public API: outside users can import `DELIVERY_REGISTER_BLOCK` and `SUBAGENT_RESULT_DESCRIPTION` from `ufo.sdk.delivery_register`, while the real definitions remain in `ufo.runtime.turns.delivery_register`.


### `core/src/ufo/sdk/flags.py`

`util` · `cross-cutting`

This file is a small public doorway into the project’s feature-flag system. A feature flag is a switch that lets the software turn a feature on or off, often depending on what a backend service says. Instead of asking outside code to import directly from the internal `ufo.flags` module, this file exposes the approved names through the SDK path.

It re-exports three things: `flag_enabled`, which is the helper used to decide whether a flag is on, and `SERVED_TRUE` / `SERVED_FALSE`, which are the two served values the backend can use to say “yes, enable this” or “no, keep this disabled.” Think of this file like a labeled service counter at the front of a building: the actual work happens behind the counter, but users are given one clear and stable place to go.

Without this file, code using the SDK might need to know the project’s internal module layout. That would make future refactors harder, because moving `ufo.flags` could break users. By re-exporting the names here, the project can keep a cleaner public API while preserving freedom to change internals later.


### HTTP and browser callbacks
Public web helpers support extension routes, session-cookie handling, and browser completion pages for provider flows.

### `core/src/ufo/sdk/callback_page.py`

`io_transport` · `request handling`

This file exists for a narrow but important moment: a person has left Slack, a command-line flow, or another conversation to approve something in a browser, and now the browser has returned to UFO. At that point there may be no normal login session or chat context attached to the page. The page can only speak based on the callback result it was given.

The file defines a tiny HTML template with a UFO logo, a headline, an optional detail line, an optional link, and optional browser behavior. It avoids extra styling files or fonts so the page loads quickly, even on a phone. It also follows the user’s light or dark theme using the browser’s built-in color support.

The main helper, `callback_page`, fills in this template safely and returns it as an HTML response. A small `PageLink` data object represents a visible link back to the conversation.

The tricky part is what happens after success. Some consent windows were opened by UFO’s own portal and can close themselves. Other tabs were opened directly by the user, for example from a chat link, and browsers usually refuse to let scripts close those. The `forward_script` function tells these cases apart using a marker in browser session storage, like checking whether a visitor still has the hand stamp from the event entrance. If it is UFO’s own consent window, it closes. Otherwise, it redirects the person back to the supplied URL.

#### Function details

##### `forward_script`  (lines 74–104)

```
def forward_script(url: str) -> str
```

**Purpose**: This function creates the small browser script used after a callback page is shown. It decides whether the current browser window should close itself or move on to a given URL.

**Data flow**: It receives a destination URL. It turns that URL into a safe JavaScript string, also protecting against characters that could accidentally break out of the script. It then returns an HTML `<script>` block that waits briefly, checks browser session storage for UFO’s consent-window marker, and either closes the window or redirects the browser to the URL.

**Call relations**: This function is used by `callback_page` when the page should automatically move the user along. Inside, it relies on `json.dumps` to quote the URL and marker safely for JavaScript, then hands the finished script back to `callback_page` to place into the HTML response.

*Call graph*: called by 1 (callback_page); 1 external calls (dumps).


##### `callback_page`  (lines 107–130)

```
def callback_page(*, headline: str, detail: str='', link: PageLink | None=None, status: int=200, close: bool=False, forward: str='') -> HTMLResponse
```

**Purpose**: This function builds the actual callback web page that a user sees after finishing a browser-based consent or install flow. It shows a headline, optional details, an optional return link, and optional automatic close or redirect behavior.

**Data flow**: It receives the text to show, an optional `PageLink`, an HTTP status code, and flags saying whether the page should close or forward. It escapes user-facing text and link values so they are treated as page content, not executable HTML. It fills the shared page template, adds a close script or a forwarding script if requested, and returns an `HTMLResponse` containing the completed page and status code.

**Call relations**: This is the main entry point for other callback routes that need to answer the browser. When forwarding is requested, it calls `forward_script` to create the correct browser behavior. It also uses `html.escape` to make inserted text safe and wraps the finished HTML in `HTMLResponse` so the web layer can send it to the browser.

*Call graph*: calls 1 internal fn (forward_script); 2 external calls (escape, HTMLResponse).


### `core/src/ufo/sdk/http.py`

`io_transport` · `request handling`

This file acts like the front desk for HTTP code in the UFO SDK. Instead of an extension reaching directly into Starlette, the web framework underneath, it imports common web building blocks from here: requests, normal responses, HTML responses, JSON responses, redirects, streaming responses, uploaded files, and form data. That keeps the public interface stable and makes route handlers easier to understand.

The file also protects one especially sensitive thing: session cookies. A session cookie is the small browser-stored token that proves a user is connected to a session. If it is set too broadly, it might leak across subdomains. If it is marked insecurely, it may travel where it should not. If it is marked as secure while served from plain local HTTP, the browser may simply reject it. To avoid every caller making those choices differently, this file provides the approved helper for setting UFO’s own session cookies.

Two small helpers decide when plain HTTP is acceptable and when the browser cookie should use the Secure flag. The important idea is that these decisions are based on the published public address of the surface, not on the incoming internal request, because production traffic may arrive internally as plain HTTP after secure HTTPS was already handled by a proxy.

#### Function details

##### `cookie_secure`  (lines 24–33)

```
def cookie_secure(published_scheme: str) -> bool
```

**Purpose**: This function decides whether a session cookie should be marked Secure, meaning the browser should only send it over HTTPS. It uses the public scheme the surface claims to publish under, not the scheme seen on an internal request.

**Data flow**: It receives a published scheme such as "http" or "https". If the scheme is exactly plain "http", it returns false, because a Secure cookie would not work on that origin. For anything else, it returns true, so the cookie keeps the safer Secure setting.

**Call relations**: Other code that prepares session cookies uses this helper before calling the cookie-setting path. It does not call out to other project code; it simply turns the published scheme into the yes-or-no Secure choice that set_session_cookie later applies.


##### `plain_local`  (lines 36–43)

```
def plain_local(published_base: str | None) -> bool
```

**Purpose**: This function checks whether a published base URL is plain HTTP but only for a local development host. It identifies the special case where not using HTTPS is acceptable because the address stays on the developer’s own machine.

**Data flow**: It receives a published base URL, or nothing. It splits that URL into pieces, then checks two things: the scheme must be "http", and the hostname must be "localhost" or end in ".localhost". It returns true only when both are true; otherwise it returns false.

**Call relations**: Code that validates or reasons about a published surface can call this when deciding whether a plain-HTTP base is allowed. Its one outside helper is urllib.parse.urlsplit, which breaks the URL into parts so the scheme and hostname can be checked safely.

*Call graph*: 1 external calls (urlsplit).


##### `set_session_cookie`  (lines 46–65)

```
def set_session_cookie(response: Response, name: str, token: str, *, samesite: Literal['lax', 'strict', 'none'], secure: bool) -> None
```

**Purpose**: This is the approved way for UFO code to attach a session cookie to an HTTP response. It deliberately avoids setting a cookie domain, so the cookie stays tied to the exact host that issued it instead of spreading across parent domains or sibling subdomains.

**Data flow**: It receives a response object, a cookie name, a session token, and cookie policy choices for SameSite and Secure. It writes a Set-Cookie header onto the response with HttpOnly always enabled, meaning browser JavaScript cannot read the cookie. It changes the response in place and returns nothing.

**Call relations**: Route or session code calls this when it needs the browser to remember a UFO session. This function then hands the actual header-writing work to Starlette’s Response.set_cookie, but only after enforcing UFO’s safer defaults: host-only cookies and HttpOnly on every session cookie.

*Call graph*: 1 external calls (set_cookie).


### Extension runtime helpers
Small public helper modules expose approved observability, scheduled-fire key handling, and untrusted display-text wrapping.

### `core/src/ufo/sdk/o11y.py`

`io_transport` · `cross-cutting`

This file is a public-facing shortcut for observability, often shortened to “o11y.” Observability means the signals a system produces so people can understand what it is doing, such as logs, warnings, metrics, and timing profiles. Extensions need to report useful information, but the project does not want every extension inventing its own metric names. If that happened, dashboards and alerts would become messy and hard to trust, like everyone labeling boxes in a warehouse with their own private naming system.

So this file re-exports a small set of approved tools from the internal harness layer: logging, warnings, metric emission, and turn profiling. Re-exporting means it imports those tools and makes them available from this SDK module, so extension code can use a stable public path instead of reaching into internal project machinery.

The important design choice is that the counter registry stays in core. An extension can emit only a metric name that core already knows about. If it tries to invent one, the system fails loudly rather than silently creating a new, untracked signal. Without this file, extension authors would either depend on internal modules directly or lack a clean, enforced way to report runtime behavior.


### `core/src/ufo/sdk/scheduled_fire.py`

`other` · `cross-cutting, whenever scheduled task runs are keyed or decoded`

This is a small doorway file. The real code lives deeper in the runtime package, but users of the SDK should not need to know that internal location. Instead, they can import `scheduled_fire_key` and `scheduled_fire_task_id` from this stable SDK path.

A “scheduled fire” is a run started by a schedule, like a cron job. Each such run needs a reliable key, a bit like a label on a package, so the system can admit it, track it, and connect it back to the scheduled task that caused it. One imported helper builds that label. The other reads the label and recovers the task id from it.

Without this file, callers would have to import from `ufo.runtime.ext.scheduled_fire` directly. That would leak internal project layout into user code, making future refactors harder. This file keeps the public face clean: the SDK promises these names here, while the implementation can remain elsewhere.


### `core/src/ufo/sdk/untrusted.py`

`util` · `cross-cutting`

This is a tiny bridge file, but it protects an important boundary. In this system, some text may come from places the program should not fully trust, such as a tool's printed output, a third-party provider response, or data returned by an extension. That text may still need to be shown to a user or passed through a renderer, but it should be clearly fenced off so nobody mistakes it for trusted instructions from the system itself.

The file imports the shared `wall` definition from `ufo.harness.untrusted` and exposes it under the SDK path. In plain terms, it is like putting the same warning label dispenser at two service counters. Core code and extension code both reach for the same label, so the warning looks and behaves consistently everywhere.

Without this file, SDK users might import from an internal harness module directly, or create their own version of the untrusted-content wrapper. That would make the project more fragile and could lead to different parts of the system marking risky text in different ways. By re-exporting `wall` here, the SDK offers a stable, public place to get the shared untrusted-content marker.
