# User onboarding and workspace enrollment  `stage-4`

This stage is the front door for new users. It runs during first setup, before the client is fully connected. The main gateway web server guides a person from installing UFO to joining the right workspace. The terminal client receives simple text instructions from gateway_directives, while gateway_web shows the same flow in a browser and translates those instructions into web-friendly data.

The email path checks that the address looks like a real work email, sends invite messages, and uses WorkOS, an external email sign-in service, to prove the person controls that address. gateway_claim records each attempt as a short-lived claim in PostgreSQL through gateway_store, then marks it verified or expired. gateway_invite manages one-time links that let a company domain create a workspace.

Once an email is verified, gateway_shared finds or creates the matching workspace and adds the user. gateway_token creates a 30-day sign-in token for the client. core onboarding then creates the workspace’s first admin, assistant, secrets, and extension setup. Finally, gateway_slack_connect can invite the new customer into UFO’s operator Slack workspace.

## Files in this stage

### Gateway surfaces
The public gateway entrypoint and its terminal/browser response formats guide a new user through onboarding.

### `control/src/ufo_control/gateway.py`

`entrypoint` · `startup, request handling, teardown`

This file is the front door for onboarding. It exposes a FastAPI web application, which is a Python web server framework, with routes for health checks, installer downloads, browser login, WorkOS/Google sign-in callbacks, and terminal or web onboarding messages. The main idea is one shared onboarding machine used by both the command-line installer and the browser page. A person first gets a session, then enters or proves a work email, then the server checks whether that email is allowed, verifies a code or Google sign-in, finds an existing workspace or creates one, and finally returns a signed token plus workspace information. Think of it like a reception desk: it checks identity, checks whether an invitation is needed, points the visitor to the right room, and hands them a badge. The file also protects important boundaries. Browser sessions are sealed so users cannot invent trusted session IDs. Request bodies and header lengths are capped so a client cannot send unlimited data. New workspace creation can be invite-gated, defaulting to “closed” unless explicitly opened. Startup builds all the needed dependencies: database pools, email verification, invite storage, workspace lookup, token signing, and optional Slack Connect delivery. Shutdown cancels background work and closes database connections.

#### Function details

##### `_stamp_script`  (lines 106–108)

```
def _stamp_script(text: str) -> str
```

**Purpose**: This prepares the downloadable installer script so it points at the public URL for the current deployment. It lets the same script template work in production, staging, or local environments.

**Data flow**: It takes the raw script text, reads the public base URL from the environment or uses the default, replaces the built-in URL placeholder once, and returns the adjusted script text.

**Call relations**: The module uses this when it creates the stamped installer script during import. Later, the script-serving route sends that already-prepared text to clients.


##### `Onboarding.advance`  (lines 125–136)

```
async def advance(self, channel: str, session: str, body: str, install: bytes) -> bytes
```

**Purpose**: This is the main stepper for onboarding. Given a user’s current channel and session, it decides whether to ask for an email, verify a code, or finish workspace sign-in.

**Data flow**: It receives a channel name, session ID, submitted text, and optional installer instructions. It looks up the live claim for that session, then routes the request to the correct next step. It returns rendered bytes that the terminal or web page can display.

**Call relations**: The web and terminal onboarding routes call this after reading a request. It hands off to _collect_email when there is no claim yet, to _verify_code when the email has not been verified, and to _resolve once the claim is verified.

*Call graph*: calls 3 internal fn (_collect_email, _resolve, _verify_code).


##### `Onboarding._collect_email`  (lines 138–160)

```
async def _collect_email(self, channel: str, session: str, body: str, install: bytes) -> bytes
```

**Purpose**: This performs the first onboarding step: ask for a work email and, once one is supplied, start the verification process. It refuses bad or disallowed emails before any code is sent.

**Data flow**: It receives the channel, session, typed body, and installer bytes. If the body is empty, it returns instructions asking for an email. If an email is present, it asks the claim workflow to start verification, catches policy or claim errors, and returns either an error plus another prompt or a message saying a code was emailed.

**Call relations**: Onboarding.advance calls this for a session that has no active claim. It uses the directive renderer to produce the small command language understood by the terminal client and web renderer.

*Call graph*: called by 1 (advance); 2 external calls (directive, render).


##### `Onboarding._verify_code`  (lines 162–171)

```
async def _verify_code(self, claim: OnboardClaim, body: str, install: bytes) -> bytes
```

**Purpose**: This checks the code sent to the user’s work email. It also handles the case where the claim was already verified by another path, such as a browser sign-in callback.

**Data flow**: It receives an existing claim, the submitted code, and installer bytes. It asks the claim workflow to verify the code. If verification fails, it reloads the current claim to decide whether to continue, ask for a code again, or restart with email entry. If verification succeeds, it moves on to workspace resolution.

**Call relations**: Onboarding.advance calls this for claims that exist but are not verified yet. On success, or if it discovers the claim has already become verified, it calls _resolve.

*Call graph*: calls 1 internal fn (_resolve); called by 1 (advance); 2 external calls (directive, render).


##### `Onboarding._resolve`  (lines 173–207)

```
async def _resolve(self, claim: OnboardClaim, body: str, install: bytes) -> bytes
```

**Purpose**: This turns a verified email into membership in a workspace. It chooses whether to join an existing workspace, ask the user to pick one, or create a new one if allowed.

**Data flow**: It receives a verified claim, the user’s latest answer, and installer bytes. It asks the workspace service for matching choices, checks whether invites allow creation, may redeem an invite, creates or joins a workspace, marks the claim complete, and returns the final signed-in response.

**Call relations**: Onboarding.advance calls this for already verified claims, and _verify_code calls it after successful verification. It uses _invite_gate when new workspace creation may be restricted, and _signed_in to produce the final token and workspace instructions.

*Call graph*: calls 2 internal fn (_invite_gate, _signed_in); called by 2 (_verify_code, advance); 2 external calls (directive, render).


##### `Onboarding._invite_gate`  (lines 209–246)

```
async def _invite_gate(self, claim: OnboardClaim, install: bytes) -> bytes | None
```

**Purpose**: This enforces the rule that some domains need an invite before a new workspace can be opened. It returns either permission to continue or a clear refusal message.

**Data flow**: It receives a verified claim and installer bytes. If the claim already has an invite, it allows the flow to continue. Otherwise it tries to redeem an invite for the email domain, then returns no value for success or rendered messages explaining an expired, consumed, missing, or otherwise unusable invite.

**Call relations**: _resolve calls this before creating a workspace when invites are required. It uses directives and rendering to turn the invite decision into something the user can read.

*Call graph*: called by 1 (_resolve); 2 external calls (directive, render).


##### `Onboarding._signed_in`  (lines 248–273)

```
def _signed_in(self, claim: OnboardClaim, ensured: EnsuredWorkspace, install: bytes) -> bytes
```

**Purpose**: This creates the final onboarding response after a workspace has been joined or created. It gives the client the token and workspace URL needed to start using the product.

**Data flow**: It receives the verified claim, the ensured workspace record, and installer bytes. It mints a bearer token, adds workspace and optional debugger information, announces the signed-in email, and chooses the next prompt or menu based on whether the user is an admin and which surface they used.

**Call relations**: _resolve calls this once workspace membership is settled. It relies on token minting for the credential and directive rendering for the final client-facing instructions.

*Call graph*: called by 1 (_resolve); 3 external calls (directive, render, mint_token).


##### `GatewayState.healthy`  (lines 283–291)

```
async def healthy(self) -> bool
```

**Purpose**: This checks whether the gateway’s database connections are alive and using the expected database roles. It supports the /healthz route used by deployment systems.

**Data flow**: It reads from the owner database pool and opens a workspace transaction, asking each database connection who the current user is. If either query fails, it logs the failure and returns false. Otherwise it returns whether both roles match the expected values stored at startup.

**Call relations**: The health-check route calls this on demand. It uses the shared workspace transaction helper and a simple SQL query to prove both database paths are usable.

*Call graph*: 2 external calls (text, workspace_tx).


##### `_require_env`  (lines 294–298)

```
def _require_env(name: str) -> str
```

**Purpose**: This reads a required environment variable and fails fast if it is missing. It prevents the server from starting in a half-configured state.

**Data flow**: It receives an environment variable name, looks it up, and returns the value if present. If the value is empty or missing, it raises a runtime error naming the missing setting.

**Call relations**: The startup lifespan calls this while building the gateway state, before accepting requests. That makes configuration mistakes appear during startup rather than during a user’s onboarding attempt.

*Call graph*: called by 1 (lifespan).


##### `_invite_required`  (lines 301–310)

```
def _invite_required() -> bool
```

**Purpose**: This decides whether new workspace creation requires an invite. It is deliberately cautious: unset means invites are required.

**Data flow**: It reads the invite-required environment variable, normalizes common true and false values, and returns a boolean. If the value is something else, it raises an error instead of guessing.

**Call relations**: The startup lifespan calls this once and stores the decision in the onboarding object. Later, workspace resolution uses that stored policy when deciding whether to create a new workspace.

*Call graph*: called by 1 (lifespan).


##### `_dsn_role`  (lines 313–317)

```
def _dsn_role(dsn: str) -> str
```

**Purpose**: This extracts the database username, also called the role, from a database connection string. The health check later uses this expected role to catch wrong database wiring.

**Data flow**: It receives a database DSN string, parses it as a SQLAlchemy URL, and returns the username part. If the DSN has no username, it raises an error.

**Call relations**: The startup lifespan calls this for both owner and serving database URLs. GatewayState.healthy later compares live database users against these saved roles.

*Call graph*: called by 1 (lifespan).


##### `_request_body`  (lines 320–326)

```
async def _request_body(request: Request) -> str
```

**Purpose**: This safely reads a small text request body. It protects the onboarding endpoints from oversized submissions.

**Data flow**: It receives a FastAPI request and reads its stream chunk by chunk. It keeps only up to the allowed maximum plus one byte, raises a request input error if the limit is exceeded, decodes the bytes as UTF-8 with replacement for invalid bytes, strips surrounding whitespace, and returns the text.

**Call relations**: Both the web onboarding route and the generic channel onboarding route call this before passing user input to Onboarding.advance. When it raises an input error, those routes turn the error into a user-facing message.

*Call graph*: called by 2 (onboard, onboard_web); 2 external calls (__init__, stream).


##### `_onboard_session`  (lines 329–341)

```
def _onboard_session(request: Request, secret: str) -> str | None
```

**Purpose**: This finds a trustworthy onboarding session in the browser’s cookies. It only accepts a cookie value that can be opened with the server’s secret.

**Data flow**: It receives a request and the signing secret. It scans the Cookie header for the onboarding cookie name, tests each matching value with the session opener, and returns the first valid sealed value. If none are valid, it returns nothing.

**Call relations**: The WorkOS callback and web onboarding route call this when they need to connect a browser request to an existing onboarding session. It uses the WorkOS session-opening helper to reject forged or stale cookie values.

*Call graph*: called by 2 (auth_callback, onboard_web); 1 external calls (open_session).


##### `gateway_app`  (lines 344–580)

```
def gateway_app() -> FastAPI
```

**Purpose**: This builds the FastAPI application and defines all HTTP routes for the gateway. It is the factory that turns the onboarding pieces into a running web service.

**Data flow**: It creates a state holder, defines startup and shutdown behavior, registers routes for health checks, downloads, login, WorkOS authentication, and onboarding, and returns the configured FastAPI app.

**Call relations**: The file calls this at the bottom to create the module-level app object used by the server runner. Inside it, route functions call helpers and the Onboarding object as requests arrive.

*Call graph*: 2 external calls (FastAPI, workos_console_mode).


##### `gateway_app.lifespan`  (lines 348–400)

```
async def lifespan(app: FastAPI)
```

**Purpose**: This is the startup and shutdown wrapper for the web server. It assembles all shared services before requests begin and cleans them up when the server stops.

**Data flow**: On startup it reads database URLs and environment settings, checks the control schema, opens a database pool, creates stores, invite services, workspace services, verification services, and token settings, initializes the serving database connection, and optionally starts Slack delivery in the background. On shutdown it cancels that background task, clears state, disposes database resources, and closes the pool.

**Call relations**: FastAPI runs this automatically around the app’s lifetime. It calls the environment and DSN helpers, constructs GatewayState and Onboarding, and makes the resulting state available to all route handlers.

*Call graph*: calls 3 internal fn (_dsn_role, _invite_required, _require_env); 18 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, create_task, gather, create_pool (+8 more)).


##### `gateway_app.healthz`  (lines 405–408)

```
async def healthz() -> Response
```

**Purpose**: This answers whether the gateway is ready and connected correctly. Deployment tools can call it to decide whether the service should receive traffic.

**Data flow**: It checks whether startup state exists and whether GatewayState.healthy succeeds. It returns JSON with status ok, or JSON with status unavailable and HTTP 503 if the service is not ready.

**Call relations**: FastAPI calls this when a client requests /healthz. It delegates the real database-role check to GatewayState.healthy and wraps the result as a JSON response.

*Call graph*: 1 external calls (JSONResponse).


##### `gateway_app.serve_script`  (lines 411–412)

```
async def serve_script() -> Response
```

**Purpose**: This serves the shell installer script. It lets users install or start the UFO client with a simple download request.

**Data flow**: It takes no request-specific input beyond the HTTP request itself. It returns the already stamped script text as plain text with a shell-script media type.

**Call relations**: FastAPI calls this for GET /ufo. It relies on the module-level script prepared earlier by _stamp_script.

*Call graph*: 1 external calls (PlainTextResponse).


##### `gateway_app.client_binary`  (lines 415–426)

```
async def client_binary(target: str) -> Response
```

**Purpose**: This serves a native UFO client binary for a specific platform target, such as macOS, Linux, or Windows. It refuses unknown targets and unconfigured deployments the same way.

**Data flow**: It receives the target from the URL, checks that the target is in the allowed set and that a binary directory is configured, chooses the expected filename, checks that the file exists, and returns the file. If any check fails, it returns a 404 plain-text refusal.

**Call relations**: FastAPI calls this for GET /ufo/bin/{target}. It uses Path to locate the file and FileResponse to stream it when present.

*Call graph*: 3 external calls (Path, FileResponse, PlainTextResponse).


##### `gateway_app.fleet`  (lines 429–432)

```
async def fleet() -> Response
```

**Purpose**: This returns a simple count of workspaces. It appears to be a small operational or public status endpoint.

**Data flow**: It reads the current gateway state, queries the database pool for the number of rows in the workspace table, and returns that count as JSON under the key craft.

**Call relations**: FastAPI calls this for GET /fleet. It uses the already-created database pool from gateway startup.

*Call graph*: 1 external calls (JSONResponse).


##### `gateway_app.login`  (lines 435–436)

```
async def login() -> Response
```

**Purpose**: This serves the browser login page. It gives web users the page that can drive onboarding and start Google sign-in.

**Data flow**: It returns the static login page HTML as an HTML response.

**Call relations**: FastAPI calls this for GET /login. Other flows, especially the authentication callback, redirect users back to this page with optional messages in the query string.

*Call graph*: 1 external calls (HTMLResponse).


##### `gateway_app.auth_start`  (lines 439–466)

```
async def auth_start(request: Request) -> Response
```

**Purpose**: This starts browser sign-in through WorkOS and Google. It creates a sealed onboarding session, stores it in a secure cookie, and redirects the browser to the external sign-in flow.

**Data flow**: It reads optional conversation and artifact query parameters, creates a random session, seals it with the server secret, packs that session and carry data into signed state, builds a WorkOS authorization URL, sets the sealed session cookie, and returns a redirect.

**Call relations**: FastAPI calls this when the login page’s Google button points to the auth start path. The later auth_callback must see the same sealed session cookie and signed state before it will trust the returned email.

*Call graph*: 7 external calls (__init__, token_urlsafe, RedirectResponse, set_session_cookie, pack_state, seal_session, unpack_state).


##### `gateway_app.auth_console`  (lines 471–475)

```
async def auth_console(request: Request) -> Response
```

**Purpose**: This is a development-only stand-in for the external Google sign-in page. It lets a local developer type a work email while still exercising the callback flow.

**Data flow**: It reads the signed state query parameter and returns an HTML console sign-in page containing that state.

**Call relations**: gateway_app registers this route only when WorkOS console mode is enabled. FastAPI calls it in local console-mode flows, and it feeds back into the same callback path as the real provider flow.

*Call graph*: 2 external calls (HTMLResponse, console_signin_page).


##### `gateway_app.auth_callback`  (lines 478–513)

```
async def auth_callback(request: Request) -> Response
```

**Purpose**: This receives the browser after WorkOS or Google sign-in. It verifies that the callback belongs to the same browser session that started the flow before admitting the email as verified.

**Data flow**: It reads the code and signed state from the query string, unpacks the state, rebuilds any login-page carry parameters, reads the sealed onboarding cookie, compares it to the state session in constant time, exchanges the code for an email, and records a verified claim. If anything fails, it adds an error message. It then redirects back to /login.

**Call relations**: FastAPI calls this for the authentication callback path. It uses _onboard_session to bind the return to the browser, and on success it hands the verified email to the onboarding claim workflow so later web onboarding can resolve the workspace.

*Call graph*: calls 1 internal fn (_onboard_session); 6 external calls (__init__, compare_digest, PlainTextResponse, RedirectResponse, unpack_state, urlencode).


##### `gateway_app.onboard_web`  (lines 516–548)

```
async def onboard_web(request: Request) -> Response
```

**Purpose**: This is the browser page’s onboarding endpoint. It advances the shared onboarding machine and returns structured directives that the page can render.

**Data flow**: It reads or mints a sealed browser onboarding session, but only reuses an existing session if it already has a live claim. It reads the small request body, calls Onboarding.advance for the web channel, parses the rendered directive bytes into JSON-friendly data, and sets a new session cookie if one was minted. Errors become visible onboarding messages instead of raw server failures.

**Call relations**: FastAPI calls this for POST /v1/onboard/web. It uses _onboard_session and _request_body before handing control to Onboarding.advance, then uses parse_directives so the browser gets JSON rather than terminal text.

*Call graph*: calls 2 internal fn (_onboard_session, _request_body); 7 external calls (token_urlsafe, JSONResponse, set_session_cookie, directive, render, parse_directives, seal_session).


##### `gateway_app.onboard`  (lines 551–578)

```
async def onboard(channel: str, request: Request) -> Response
```

**Purpose**: This is the generic onboarding endpoint for non-web surfaces, especially the terminal client. It advances onboarding using a channel and session supplied by request metadata.

**Data flow**: It reads the channel from the URL, the session from the x-ufo-session header, and installer details from headers. It rejects missing sessions, overlong channel or session values, and oversized bodies. Otherwise it reads the body, calls Onboarding.advance, and returns the rendered directives as plain text.

**Call relations**: FastAPI calls this for POST /v1/onboard/{channel}. It prepares client-install information and safe input, then delegates the actual onboarding decisions to Onboarding.advance.

*Call graph*: calls 1 internal fn (_request_body); 5 external calls (__init__, PlainTextResponse, client_install, directive, render).


### `control/src/ufo_control/gateway_directives.py`

`io_transport` · `request handling`

The UFO server can send simple commands, called directives, to a terminal client. This file is the place where those commands are turned into bytes, which are the raw data sent over the connection. Think of it like writing short notes in a very strict format so the client can read them reliably.

A directive is one line of text. It starts with a command word, then optional fields separated by tab characters, and ends with a newline. Because user or server text might itself contain tabs, newlines, or backslashes, the file carefully escapes those characters first. Without that escaping, the client could misunderstand where one field ends or where one directive line ends.

The file also includes a small helper for reading HTTP-style headers without caring about letter case, because headers like `X-UFO-Installed` and `x-ufo-installed` should mean the same thing.

The most important decision here is in `client_install`. It checks request headers from the client. If the client says it is not installed, the server sends an `install` directive. If the server is serving a specific client version and the client reports a different version, it also sends `install`, which acts like an update prompt. If everything already matches, it sends nothing.

#### Function details

##### `directive`  (lines 10–15)

```
def directive(verb: str, *fields: str) -> bytes
```

**Purpose**: Builds one server-to-client instruction line. It turns a command word and its extra text fields into a safe byte string the terminal client can parse.

**Data flow**: It receives a directive verb, such as `install`, plus any number of text fields. It cleans each field by escaping backslashes, tabs, and newlines, removes carriage returns, joins everything with tab characters, adds a final newline, and returns the result as bytes.

**Call relations**: When `client_install` decides the terminal client should install or update itself, it asks `directive` to produce the exact `install` instruction that will be sent back to the client.

*Call graph*: called by 1 (client_install).


##### `render`  (lines 18–19)

```
def render(*lines: bytes) -> bytes
```

**Purpose**: Combines several already-built directive byte strings into one byte string. This is useful when a response needs to contain multiple instructions in order.

**Data flow**: It receives any number of byte strings. It joins them together without adding anything extra and returns the combined bytes.

**Call relations**: This helper is not shown as being called by the listed functions, but it is meant to be the final glue step when several directive lines need to be sent as one response.


##### `header_value`  (lines 22–27)

```
def header_value(headers: Mapping[str, str], name: str) -> str | None
```

**Purpose**: Looks up a header value without caring about capitalization. This matters because HTTP-style header names are commonly treated as case-insensitive.

**Data flow**: It receives a mapping of header names to values and the header name to find. It lowercases the requested name, compares it with each available header name lowercased, and returns the matching value if found; otherwise it returns `None`.

**Call relations**: The `client_install` function uses this helper to read the client’s installation and script-version headers reliably, even if their capitalization differs.

*Call graph*: called by 1 (client_install).


##### `client_install`  (lines 30–40)

```
def client_install(headers: Mapping[str, str]) -> bytes
```

**Purpose**: Decides whether the server should tell the terminal client to install or update itself. It sends an `install` directive only when the client is missing or out of date.

**Data flow**: It receives the client request headers. First it checks whether `x-ufo-installed` equals `1`; if not, it returns an encoded `install` directive. If the client is installed, it reads the server’s expected client version from the `UFO_CLIENT_VERSION` environment variable. When that version is set and the client’s reported `x-ufo-script` version does not match, it returns the same `install` directive. If no install or update is needed, it returns empty bytes.

**Call relations**: This is the decision point for self-install behavior. It calls `header_value` to read the relevant headers safely, and it calls `directive` when it needs to produce the actual instruction that the client will receive.

*Call graph*: calls 2 internal fn (directive, header_value).


### `control/src/ufo_control/gateway_web.py`

`io_transport` · `request handling`

This file is the web “front window” for onboarding. The real sign-in journey is run by an onboarding state machine elsewhere. That machine speaks in simple directive lines such as “say this”, “ask this”, “here is the token”, or “here is the workspace”. This file helps the browser understand those directives and includes the full HTML, CSS, and JavaScript for the login page.

The page starts by calling the web onboarding endpoint, then shows whatever the machine asks for: an email address, a code, or a choice. It also offers “Continue with Google”, which sends the user through the OAuth sign-in path. OAuth means a trusted outside login provider confirms who the user is, rather than this page collecting a password.

When onboarding succeeds, the page switches from the sign-in card to a signed-in home card. It shows the member email, workspace URL, a terminal install command, and sometimes an operator-only session debugger button. Tokens are sent through form posts, not placed in URLs, which helps avoid leaking them through browser history or copied links.

A key idea is that the web page is only a renderer. Like a receptionist reading instructions from the same checklist as the terminal client, it displays prompts and sends answers back, but it does not invent its own onboarding rules.

#### Function details

##### `parse_directives`  (lines 35–44)

```
def parse_directives(payload: bytes) -> list[dict[str, object]]
```

**Purpose**: This function converts raw directive text into a list of plain dictionaries that can be returned as JSON to the browser. It preserves special characters inside fields, so prompts or messages containing tabs or newlines do not get damaged by the line-based format.

**Data flow**: It receives bytes containing directive lines. It decodes them into text, skips blank lines, splits each line into a verb and tab-separated fields, and runs each field through _unescape to restore characters that were safely encoded. It returns a list like “verb plus fields”, ready for JSON use by the web page.

**Call relations**: This is the outward-facing parser in this file. As it reads each directive field, it hands the field to _unescape so the low-level escape decoding stays in one small helper instead of being mixed into the line parsing.

*Call graph*: calls 1 internal fn (_unescape).


##### `_unescape`  (lines 47–60)

```
def _unescape(field: str) -> str
```

**Purpose**: This function reverses the small escaping scheme used inside directive fields. It turns written escape sequences such as backslash-t and backslash-n back into real tab and newline characters.

**Data flow**: It receives one escaped string field. It walks through the string character by character, replacing recognized backslash sequences with their real characters and leaving everything else alone. It returns the cleaned-up string.

**Call relations**: parse_directives calls this for every field it extracts from a directive line. _unescape does the detailed character repair, then hands the restored field back so parse_directives can build the final browser-ready directive objects.

*Call graph*: called by 1 (parse_directives).


### Email proof and invites
Email claims, invite validation, delivery, persistence, and WorkOS verification prove that the user controls an eligible work address.

### `control/src/ufo_control/gateway_claim.py`

`domain_logic` · `request handling during onboarding email verification`

This file protects onboarding from two common problems: people using an email address that is not allowed, and stale or competing verification attempts confusing the user. A claim is like a temporary ticket at a service desk. It records the email, where the person started onboarding, when the ticket expires, and whether the email has been proven.

The workflow first checks the email against the project’s work-email rules. If the email is acceptable, it stores a new claim and asks WorkOS, the outside identity service, to start verification. If WorkOS cannot start, the file cleans up the claim so no dead ticket is left behind.

For code-based verification, the file treats ten minutes as the claim’s lifetime, matching WorkOS Magic Auth code timing. WorkOS does not clearly distinguish a wrong code from an expired one, so this file uses the claim’s expiry time to give a better message: before expiry, a failed code means “try again”; after expiry, the onboarding attempt must restart.

It also guards against races, where two browser tabs or attempts touch the same claim. Important writes are conditional, meaning only the still-current unverified claim can be changed. If another attempt got there first, the user sees a clear “session changed” message instead of silently corrupting state.

#### Function details

##### `Verifier.authorization_url`  (lines 30–30)

```
def authorization_url(self, state: str) -> str
```

**Purpose**: This is part of the verifier contract. It describes a method that should build the web address where a browser can send the user to start an outside authorization flow.

**Data flow**: It receives a state value, which is usually a random marker used to connect the later callback to the original session. An implementation turns that marker into an authorization URL and returns the URL as text.

**Call relations**: This file defines the shape of the verifier that ClaimWorkflow can rely on. The concrete verifier lives elsewhere; ClaimWorkflow can use any object that follows this contract.


##### `Verifier.exchange`  (lines 31–31)

```
async def exchange(self, code: str) -> str
```

**Purpose**: This is part of the verifier contract for browser-based sign-in. It describes exchanging a callback code from the outside service for the verified email address or identity result.

**Data flow**: It receives a code from the outside authorization callback. An implementation sends that code to the verifier service, checks the response, and returns the verified email address as text.

**Call relations**: ClaimWorkflow depends on this verifier interface rather than a specific WorkOS class. That keeps the onboarding workflow separate from the details of talking to WorkOS.


##### `Verifier.begin`  (lines 32–32)

```
async def begin(self, email: str) -> None
```

**Purpose**: This is part of the verifier contract for starting an email-code verification. It asks the outside service to send or prepare a verification code for an email address.

**Data flow**: It receives an email address. An implementation contacts the verifier service and either completes successfully with no returned value or raises an error if verification cannot be started.

**Call relations**: ClaimWorkflow.start uses this capability after it has stored a new claim. If beginning verification fails, the workflow deletes the claim so the system does not keep a useless onboarding attempt.


##### `Verifier.confirm`  (lines 33–33)

```
async def confirm(self, email: str, code: str) -> bool
```

**Purpose**: This is part of the verifier contract for checking a code typed by the user. It answers whether the code proves control of the email address.

**Data flow**: It receives the email address and the code the user entered. An implementation asks the verifier service whether they match, then returns true for a valid code or false for a wrong one; it may raise an error if the service itself rejects or fails the request.

**Call relations**: ClaimWorkflow.verify calls this during code confirmation. The workflow then decides whether to mark the claim verified, tell the user the code is wrong, or end the claim because something failed.


##### `ClaimWorkflow.start`  (lines 47–57)

```
async def start(self, email: str, surface: str, surface_ref: str) -> str
```

**Purpose**: This starts a new code-based onboarding claim for a work email. It validates the email, records a temporary claim, and asks the verifier to begin sending or preparing the verification code.

**Data flow**: It takes the user’s email, the kind of place they are onboarding from, and a reference for that place. It checks the email policy, builds a new unverified claim, saves it, and calls the verifier. If the verifier fails, it deletes the saved claim and raises a user-facing ClaimError. If all goes well, it returns the accepted email domain.

**Call relations**: This is the entry into the claim flow for terminal or code-based verification. It calls ClaimWorkflow._claim to build the stored claim, hands that claim to the store, and then hands the email to the verifier. When something goes wrong, it converts the failure into ClaimError so the onboarding screen can show the message.

*Call graph*: calls 1 internal fn (_claim); 1 external calls (__init__).


##### `ClaimWorkflow.verify`  (lines 59–73)

```
async def verify(self, claim: OnboardClaim, code: str) -> None
```

**Purpose**: This checks a verification code for an existing claim and, if it is correct and still timely, marks the claim as verified. It also gives different messages for expired claims, wrong codes, outside-service errors, and competing attempts.

**Data flow**: It receives an existing claim and a code typed by the user. First it compares the current time with the claim’s expiry time. If expired, it tries to delete the unverified claim and reports expiration or a changed session. If still active, it asks the verifier to confirm the code. A false answer becomes an “incorrect code” message. A true answer leads to marking the claim verified in the store; if that write loses to another attempt, it reports that the session changed.

**Call relations**: This function is called when the user submits a code. It relies on the store for safe conditional deletes and updates, and on the verifier for the outside truth about the code. It raises ClaimError whenever the flow should stop and show the user a clear explanation.

*Call graph*: 2 external calls (__init__, now).


##### `ClaimWorkflow.admit_verified`  (lines 75–85)

```
async def admit_verified(self, email: str, surface: str, surface_ref: str) -> OnboardClaim
```

**Purpose**: This creates or reuses a claim for a browser flow where WorkOS has already verified the email before control returns here. It avoids creating duplicate live claims for the same onboarding surface.

**Data flow**: It receives a verified email plus the surface and surface reference for the onboarding session. It validates the email, asks the store whether there is already a live claim for that same session, and returns it if found. Otherwise it builds a new claim with the current time already set as verified, saves it, and returns the new claim.

**Call relations**: This is used by the browser callback path after the outside verifier has already done its work. It calls ClaimWorkflow._claim to create the stored record when needed, but first checks the store so repeated callbacks land on the same claim instead of opening parallel ones.

*Call graph*: calls 1 internal fn (_claim); 1 external calls (now).


##### `ClaimWorkflow._claim`  (lines 87–99)

```
def _claim(self, email: str, domain: str, surface: str, surface_ref: str, verified_at: datetime | None) -> OnboardClaim
```

**Purpose**: This is the small factory that builds the claim record in one consistent way. It makes sure every claim has a fresh identifier, normalized email text, an expiry time, and the right verified or unverified stamp.

**Data flow**: It receives the email, its approved domain, the onboarding surface information, and either a verification time or no verification time. It trims and lowercases the email, creates a new unique claim ID, sets expiry to now plus the claim time limit, leaves the invite ID empty, and returns an OnboardClaim object ready to store.

**Call relations**: ClaimWorkflow.start calls this to create an unverified claim before sending a code. ClaimWorkflow.admit_verified calls it to create an already-verified claim after a browser-based verification. Keeping this construction in one place helps both paths produce the same kind of stored record.

*Call graph*: called by 2 (admit_verified, start); 3 external calls (__init__, now, uuid4).


### `control/src/ufo_control/gateway_invite.py`

`domain_logic` · `admin invite creation and workspace signup`

This file is the project’s invite ledger for new workspaces. Instead of sending a secret code that anyone could copy and paste, it grants permission to an email domain, such as `example.com`. A person proves they belong to that domain by verifying their work email, and then the system can decide whether that domain is allowed to create a workspace.

The file defines the database table for invites, including who the invite was sent to, which object number it belongs to, when it expires, and whether it has already been used. It also defines small result objects such as “unknown,” “expired,” “already consumed,” and “accepted,” so callers can react clearly to each outcome.

The central class is `InviteCodes`. It can check whether a domain currently has a usable invite, create a new invite, and redeem an invite when a workspace claim is being made. The important safety feature is that redemption happens inside a database transaction with a row lock. In plain terms, the system locks the invite like a ticket booth window: while one request is using the ticket, another request cannot sneak in and use the same ticket too. It also records the invite on the workspace claim in the same transaction, so a crash cannot leave the invite marked as used but unattached to the claim.

#### Function details

##### `InviteCodes.available`  (lines 92–101)

```
async def available(self, email_domain: str) -> bool
```

**Purpose**: Checks whether a given email domain currently has an unused, unexpired invite. This is useful when the signup flow needs to know if a verified work email domain is allowed to create a workspace.

**Data flow**: It receives an email domain as text. It asks the database whether there is an invite row for that domain where `consumed_at` is still empty and the expiry time is in the future. It returns `true` if such a row exists, otherwise `false`; it does not change anything.

**Call relations**: This is a read-only lookup used by higher-level signup or gateway code before attempting workspace creation. Unlike redemption, it only answers “is there a live grant?” and does not lock, consume, or attach the invite to a claim.


##### `InviteCodes.mint`  (lines 103–137)

```
async def mint(self, object_number: int, email: str) -> MintedInvite
```

**Purpose**: Creates a new invite for one work email address and its domain. It refuses to create duplicates when the object number or domain is already known or already has a live invite.

**Data flow**: It receives an object number and an email address. It normalizes the email, checks that it is acceptable as a work email, calculates an expiry time, then opens a database transaction. Inside that transaction it checks for existing consumed or still-live invites for the same object number and domain, removes old expired unconsumed rows that would get in the way, and inserts a fresh invite with a new unique id. It returns a `MintedInvite` containing the object number, cleaned email address, and expiry time. If the invite would conflict with an existing grant, it raises `InviteError`.

**Call relations**: This is the creation path, likely called by an admin command such as an invitation command. It relies on `normalize_email` and `WorkEmailPolicy` to make sure the invite is tied to a valid work address, calls `InviteCodes._refuse_standing` to explain conflicts clearly before inserting, and uses a database uniqueness check as a final guard in case two requests race each other.

*Call graph*: calls 1 internal fn (_refuse_standing); 6 external calls (__init__, __init__, __init__, now, normalize_email, uuid4).


##### `InviteCodes._refuse_standing`  (lines 139–153)

```
async def _refuse_standing(self, connection: asyncpg.Connection, column: str, value: object, subject: str, now: datetime) -> None
```

**Purpose**: Checks whether a particular object number or email domain already has a meaningful invite record, and stops invite creation if so. It exists to give clear, human-readable reasons instead of allowing a vague database conflict.

**Data flow**: It receives an open database connection, the column to check, the value to look for, a readable subject name, and the current time. It queries the invite table for a matching row that is either already consumed or still unexpired. If none exists, it returns quietly. If the row was already consumed, it raises `InviteError` saying the subject is already identified. If the row is still live, it raises `InviteError` saying there is already an invite and when it expires.

**Call relations**: This helper is used by `InviteCodes.mint` during invite creation. It performs the careful pre-checks before `mint` deletes stale expired rows and inserts the new invite, helping the admin-facing flow report the real reason an invite cannot be issued.

*Call graph*: called by 1 (mint); 2 external calls (__init__, fetchrow).


##### `InviteCodes.redeem`  (lines 155–198)

```
async def redeem(self, email_domain: str, claim_id: UUID) -> InviteUnknown | InviteExpired | InviteConsumed | InviteAccepted
```

**Purpose**: Attempts to use the invite for an email domain when a workspace claim is being made. It turns a live invite into a consumed one and attaches it to the claim, while safely handling missing, expired, or already-used invites.

**Data flow**: It receives an email domain and a claim id. It opens a database transaction, finds the most relevant invite for that domain, and locks that row so two concurrent requests cannot use it at the same time. If there is no invite, it returns `InviteUnknown`. If the invite was already consumed, it checks whether this same claim is already attached to it; if yes, it returns `InviteAccepted` again, and if no, it returns `InviteConsumed`. If the invite is expired, it returns `InviteExpired`. If the invite is live, it stamps `consumed_at`, writes the invite id onto the claim row, and returns `InviteAccepted` with the invite id, object number, and consumption time.

**Call relations**: This is the critical path in workspace creation. Higher-level signup code calls it after a user has proved their email domain. It coordinates with the gateway claim table by writing the invite id onto the claim, and it returns one of the small result objects so the caller can decide whether to proceed, retry, or show an explanation to the user.

*Call graph*: 5 external calls (__init__, __init__, __init__, __init__, now).


### `control/src/ufo_control/gateway_email.py`

`io_transport` · `invite sending / request handling`

This file is the email front desk for the control service. First, it protects workspace invites by checking that an address is well formed and that its domain is not a common free or disposable email provider. That matters because a workspace is meant to map to a real organization, not to a personal Gmail account or a temporary inbox.

After an email passes that policy, the file can build the one message this service sends: an invite that tells the recipient how to install and sign in. The message includes the public host name, the invited email address, the email domain, and the expiry time.

For delivery, the file offers two senders. In production, `SesEmailSender` sends mail through Amazon SES, Amazon's email service. It gets short-lived AWS credentials by exchanging the pod's web identity token with STS, Amazon's security token service, then signs the SES request using AWS Signature Version 4, which is Amazon's way of proving the request is allowed. In local development, `ConsoleEmailSender` logs the email instead, like placing the letter on the counter rather than mailing it.

Configuration is deliberately strict. Missing SES settings or an unknown email mode raise errors right away, so a deployment does not pretend email is working when it is not.

#### Function details

##### `normalize_email`  (lines 118–126)

```
def normalize_email(email: str) -> tuple[str, str]
```

**Purpose**: This function cleans up an email address and extracts its domain in a safe, consistent form. It is used before policy checks and invite text generation so malformed addresses cannot slip through.

**Data flow**: It receives a raw email string. It trims surrounding spaces, lowercases it, and checks it against a strict email pattern that requires a domain shaped like a real host name. If the address is invalid, it raises `WorkEmailError`; otherwise it returns the normalized full address and the normalized domain.

**Call relations**: The work-email policy calls this before deciding whether a domain is allowed, and the invite builder calls it to identify the email's domain for the message text. It is the shared doorway that turns messy user input into a predictable form.

*Call graph*: called by 2 (validate, invite_email); 1 external calls (__init__).


##### `WorkEmailPolicy.validate`  (lines 133–137)

```
def validate(self, email: str) -> str
```

**Purpose**: This method decides whether an email address is acceptable as a work email. It rejects malformed addresses and domains known to be free personal mail or disposable mail.

**Data flow**: It receives an email string, passes it through `normalize_email`, then compares the extracted domain with its denylist. If the domain is blocked, it raises `WorkEmailError`; if it is allowed, it returns the domain.

**Call relations**: This is the policy layer that sits before inviting or admitting a workspace domain. It relies on `normalize_email` for safe parsing, then applies the business rule about what counts as a work address.

*Call graph*: calls 1 internal fn (normalize_email); 1 external calls (__init__).


##### `public_apex_host`  (lines 140–148)

```
def public_apex_host() -> str
```

**Purpose**: This function finds the public host name that should appear in invite instructions. It lets deployments override the default public site while keeping the email body free of URL scheme and path details.

**Data flow**: It reads `UFO_PUBLIC_BASE_URL` from the environment, or uses the default public URL if the variable is not set. It parses that URL and returns just the network host part. If the value is not a usable base URL, it raises an error.

**Call relations**: Code that prepares invite emails can call this to get the host value passed into `invite_email`. It delegates URL parsing to the standard library so the rest of the email-building code can work with a plain host name.

*Call graph*: 1 external calls (urlsplit).


##### `invite_email`  (lines 151–161)

```
def invite_email(email: str, expires_at: datetime, apex_host: str) -> tuple[str, str]
```

**Purpose**: This function creates the subject and plain-text body for an invitation email. It keeps the message format in one place so every invite says the same thing.

**Data flow**: It receives the invitee email, an expiry time, and the public host name. It normalizes the email to get the domain, formats the expiry time in UTC, fills those values into the invite template, and returns the subject and body text.

**Call relations**: This is the message-building step before delivery. It calls `normalize_email` to get the domain safely, then hands back text that can be sent by either the SES sender or the console sender.

*Call graph*: calls 1 internal fn (normalize_email); 1 external calls (astimezone).


##### `EmailSender.send`  (lines 165–165)

```
async def send(self, email: str, subject: str, text: str) -> None
```

**Purpose**: This is the common promise that all email senders follow: given a recipient, subject, and text body, send the message somehow. It lets the rest of the service use email without caring whether delivery is real or local-only.

**Data flow**: It is a protocol method, so it does not do work itself. It defines the expected inputs: email address, subject, and text. Implementations return nothing when sending succeeds, or raise an error if they cannot send.

**Call relations**: Both `SesEmailSender.send` and `ConsoleEmailSender.send` fit this shape. `email_sender_from_env` returns an object matching this protocol, allowing callers to treat different senders the same way.


##### `SesEmailSender.send`  (lines 188–209)

```
async def send(self, email: str, subject: str, text: str) -> None
```

**Purpose**: This method sends an email through Amazon SES. It is the production path for real outbound invite delivery.

**Data flow**: It receives a recipient email, subject, and body text. It first asks AWS STS for temporary credentials, then builds the SES JSON request, signs that request so AWS can verify it, and posts it to the SES endpoint over HTTP. If SES reports an error, it raises a runtime error containing the status and a shortened response body; otherwise it returns nothing.

**Call relations**: This is the main delivery action for the SES sender chosen by `email_sender_from_env`. It calls `_assume_role` to get credentials, `_sigv4_headers` to sign the request, and then uses `httpx.AsyncClient` for the network call.

*Call graph*: calls 2 internal fn (_assume_role, _sigv4_headers); 3 external calls (now, AsyncClient, dumps).


##### `SesEmailSender._assume_role`  (lines 211–230)

```
async def _assume_role(self) -> SesCredentials
```

**Purpose**: This method gets temporary AWS credentials for sending email. It uses the pod's web identity token, which is how Kubernetes workloads can prove who they are to AWS without storing long-lived keys.

**Data flow**: It reads the token file from disk, sends that token and the configured role ARN to AWS STS, and waits for a response. If STS returns an error, it raises a runtime error. If STS succeeds, it parses the XML response into a `SesCredentials` object.

**Call relations**: `SesEmailSender.send` calls this immediately before each SES send. After the STS network request succeeds, it hands the response text to `_parse_assume_role_credentials` so the rest of the sender can use simple credential fields.

*Call graph*: calls 1 internal fn (_parse_assume_role_credentials); called by 1 (send); 1 external calls (AsyncClient).


##### `_parse_assume_role_credentials`  (lines 233–246)

```
def _parse_assume_role_credentials(payload: str) -> SesCredentials
```

**Purpose**: This helper turns AWS STS's XML response into the three credential strings needed to sign an SES request. It also checks that the response actually contains all required fields.

**Data flow**: It receives the raw XML text from STS. It parses the XML, reads the access key, secret key, and session token from the expected credential section, and returns a `SesCredentials` value. If any credential is missing, it raises an error.

**Call relations**: `SesEmailSender._assume_role` calls this after STS accepts the web identity token. Its returned credentials are then used by `SesEmailSender.send` when building signed SES headers.

*Call graph*: called by 1 (_assume_role); 2 external calls (__init__, fromstring).


##### `_parse_assume_role_credentials.credential`  (lines 236–240)

```
def credential(name: str) -> str
```

**Purpose**: This small inner helper reads one named credential field from the parsed STS XML. It exists so all three required fields are checked in the same careful way.

**Data flow**: It receives the name of one credential field, such as `AccessKeyId`. It searches the parsed XML for that value. If it finds a non-empty value, it returns it; if not, it raises a runtime error saying the STS response is missing that field.

**Call relations**: It is used only inside `_parse_assume_role_credentials`, once for each required AWS credential value. This keeps the parsing code short while making missing-field failures clear.


##### `_sigv4_headers`  (lines 249–282)

```
def _sigv4_headers(host: str, body: bytes, region: str, credentials: SesCredentials, now: datetime) -> dict[str, str]
```

**Purpose**: This helper creates the AWS Signature Version 4 headers needed for SES to trust the request. In plain terms, it makes a tamper-proof signature over the request body, time, region, and credentials.

**Data flow**: It receives the SES host, request body bytes, AWS region, temporary credentials, and the current time. It hashes the body, builds AWS's canonical request text, derives a signing key, calculates the final signature, and returns a headers dictionary containing content metadata, the session token, date, and authorization value.

**Call relations**: `SesEmailSender.send` calls this after building the SES request body and obtaining credentials. `_sigv4_headers` calls `_signing_key` to derive the cryptographic key used for the final signature.

*Call graph*: calls 1 internal fn (_signing_key); called by 1 (send); 3 external calls (strftime, sha256, new).


##### `_signing_key`  (lines 285–289)

```
def _signing_key(secret_key: str, date_stamp: str, region: str) -> bytes
```

**Purpose**: This helper derives the special short-lived key used to sign an AWS SES request. It follows AWS's required sequence for turning a secret key, date, region, and service name into a signing key.

**Data flow**: It receives the AWS secret key, date stamp, and region. Starting with the secret key prefixed by `AWS4`, it repeatedly applies HMAC-SHA256, a standard keyed hash, with the date, region, SES service name, and final AWS marker. It returns the resulting bytes.

**Call relations**: _sigv4_headers calls this while preparing the authorization header. The returned key is not sent over the network; it is used locally to calculate the request signature.

*Call graph*: called by 1 (_sigv4_headers); 1 external calls (new).


##### `ConsoleEmailSender.send`  (lines 299–300)

```
async def send(self, email: str, subject: str, text: str) -> None
```

**Purpose**: This method pretends to send an email by writing it to the application log. It is useful for local development where a developer wants to see the invite without configuring an Amazon SES account.

**Data flow**: It receives the recipient email, subject, and body text. Instead of contacting any mail service, it writes those values to the logger and returns nothing.

**Call relations**: `email_sender_from_env` returns this sender when `UFO_CONTROL_EMAIL_MODE` is set to `console`. It follows the same `EmailSender.send` shape as the SES sender, so callers do not need separate local-development code.


##### `email_sender_from_env`  (lines 303–317)

```
def email_sender_from_env() -> EmailSender
```

**Purpose**: This function chooses which email sender the service should use based on environment variables. It is the setup switch between real SES delivery and local console logging.

**Data flow**: It reads `UFO_CONTROL_EMAIL_MODE`, defaulting to SES mode. In console mode, it returns a `ConsoleEmailSender`. In SES mode, it reads the required sender address, region, AWS role ARN, and web identity token path, then returns a configured `SesEmailSender`. If the mode is unknown or a required value is missing, it raises an error.

**Call relations**: Startup or invite setup code can call this once to obtain an `EmailSender`. It calls `_require_env` for settings that must exist, constructs the selected sender, and leaves later code to call that sender's `send` method.

*Call graph*: calls 1 internal fn (_require_env); 3 external calls (__init__, __init__, Path).


##### `_require_env`  (lines 320–324)

```
def _require_env(name: str) -> str
```

**Purpose**: This helper reads an environment variable that must be present. It makes configuration failures clear and immediate.

**Data flow**: It receives the environment variable name. It looks up the value in the process environment. If the value is missing or empty, it raises a runtime error; otherwise it returns the value.

**Call relations**: `email_sender_from_env` uses this when building the SES sender, because real email delivery cannot work without the configured sender address, AWS role, and token file path.

*Call graph*: called by 1 (email_sender_from_env).


### `control/src/ufo_control/gateway_store.py`

`io_transport` · `request handling during hosted onboarding`

This file is the database-backed notebook for hosted onboarding. When a person begins onboarding, the system needs a durable record of who they are, what outside surface they came from, whether they verified their email, and whether the process produced a workspace. Without this file, that state would live only in memory and could be lost on restart, or two active onboarding attempts for the same surface could accidentally overlap.

The table definition at the top describes the PostgreSQL shape: each claim has an id, email details, a surface and surface reference, an expiry time, optional verification time, optional resulting workspace id, and optional invite id. A unique partial index acts like a “one active ticket per counter” rule: for a given surface and reference, there can only be one unfinished claim.

`OnboardClaim` is the plain data container used by the rest of the code. `OnboardStore` is the database access object. It uses an asyncpg connection pool, meaning it borrows database connections asynchronously instead of blocking the whole program while waiting. Its methods insert new claims, find the current unfinished claim, mark a claim as email-verified, complete it with a workspace id, or delete it. The small `_aware` helper makes sure datetimes coming back from the database carry timezone information, so later time comparisons are not ambiguous.

#### Function details

##### `_aware`  (lines 42–45)

```
def _aware(value: datetime | None) -> datetime | None
```

**Purpose**: This helper makes a datetime safe to use by ensuring it has timezone information. If there is no datetime, it leaves it as missing.

**Data flow**: It receives either a datetime value or `None`. If the value is `None`, it returns `None`; if the datetime already has a timezone, it returns it unchanged; otherwise it labels it as UTC, the standard world time used for consistent comparisons.

**Call relations**: When `OnboardStore.live_claim` rebuilds an `OnboardClaim` from a database row, it calls `_aware` for stored timestamps. This keeps the returned claim’s times consistent before the claim is handed back to the onboarding flow.

*Call graph*: called by 1 (live_claim); 1 external calls (replace).


##### `OnboardStore.insert_claim`  (lines 52–65)

```
async def insert_claim(self, claim: OnboardClaim) -> None
```

**Purpose**: This saves a newly created onboarding claim into PostgreSQL. It is used when someone starts an onboarding flow and the system needs a durable record of that pending claim.

**Data flow**: It receives an `OnboardClaim` object containing the claim id, email, surface information, expiry time, and optional verification time. It borrows a database connection from the pool and inserts those fields into the onboarding claim table. It does not return a value; the change is the new row in the database.

**Call relations**: This is an entry point into the store from the wider onboarding code when a new claim is created. It does not call other project helpers; it hands the data directly to PostgreSQL through asyncpg.


##### `OnboardStore.live_claim`  (lines 67–86)

```
async def live_claim(self, surface: str, surface_ref: str) -> OnboardClaim | None
```

**Purpose**: This looks up the current unfinished onboarding claim for a given surface and surface reference. It is used when the system needs to continue or inspect an onboarding attempt that has not yet produced a workspace.

**Data flow**: It receives a surface name and a surface reference, then queries the database for a matching row whose `resulting_workspace_id` is still empty. If no row exists, it returns `None`. If a row exists, it turns the database fields back into an `OnboardClaim`, fixing timestamp timezone information along the way.

**Call relations**: The onboarding flow calls this when it needs the active claim for a particular outside surface. Inside the method, it uses `_aware` to normalize timestamps and then constructs an `OnboardClaim` object to return to the caller.

*Call graph*: calls 1 internal fn (_aware); 1 external calls (__init__).


##### `OnboardStore.mark_verified`  (lines 88–95)

```
async def mark_verified(self, claim_id: UUID) -> bool
```

**Purpose**: This records that a claim has passed email verification. It only succeeds the first time, which helps prevent repeated verification actions from being mistaken for new work.

**Data flow**: It receives a claim id, borrows a database connection, and updates that row’s `verified_at` field to the current database time only if it was previously empty. It returns `true` if it actually recorded the verification, and `false` if the claim was missing or had already been verified.

**Call relations**: The wider onboarding flow calls this after a verification step succeeds. It relies on PostgreSQL’s conditional update to make the “only once” rule reliable even if two requests arrive at nearly the same time.


##### `OnboardStore.complete`  (lines 97–103)

```
async def complete(self, claim_id: UUID, resulting_workspace_id: str) -> None
```

**Purpose**: This marks an onboarding claim as finished by attaching the workspace id that resulted from it. After this, the claim is no longer considered active.

**Data flow**: It receives a claim id and the resulting workspace id. It updates the matching database row so `resulting_workspace_id` is filled in. It returns nothing; the important effect is that future active-claim lookups will no longer treat this claim as live.

**Call relations**: The onboarding flow calls this after a workspace has been created or linked. This works together with `live_claim`, because `live_claim` only returns claims whose resulting workspace id is still empty.


##### `OnboardStore.delete_claim`  (lines 105–107)

```
async def delete_claim(self, claim_id: UUID) -> None
```

**Purpose**: This removes an onboarding claim completely, regardless of whether it was verified. It is useful for cleanup or cancellation when the system decides the claim should no longer exist.

**Data flow**: It receives a claim id, borrows a database connection, and deletes the matching row from the onboarding claim table. It returns nothing; the database row is simply gone if it existed.

**Call relations**: Other onboarding code can call this when a claim must be discarded unconditionally. It does not hand work to other project functions; it performs one direct database delete.


##### `OnboardStore.delete_unverified_claim`  (lines 109–115)

```
async def delete_unverified_claim(self, claim_id: UUID) -> bool
```

**Purpose**: This removes a claim only if it has not yet been verified. It protects verified claims from being accidentally deleted by cleanup code meant for abandoned starts.

**Data flow**: It receives a claim id and asks PostgreSQL to delete that row only when `verified_at` is still empty. It returns `true` if a row was actually deleted, and `false` if the claim was missing or had already been verified.

**Call relations**: The wider onboarding flow can call this during cancellation or cleanup of unverified attempts. Like `mark_verified`, it lets the database enforce the condition so the result stays correct even when requests happen close together.


### `control/src/ufo_control/gateway_workos.py`

`io_transport` · `startup and sign-in request handling`

This file protects onboarding sign-in from two common problems: forged browser callbacks and fake email identities. WorkOS is an outside identity service. In normal mode, this code asks WorkOS to send or check email verification codes, and to run the “Continue with Google” sign-in path. The project’s own gateway still collects the email address first, so the app can apply its work-email rules before any message is sent.

The file also signs small pieces of browser state. Think of this like putting a tamper-proof sticker on a note before handing it to the browser. Later, when the browser brings the note back, the gateway checks the sticker before trusting the session, conversation, or artifact link inside it. Cookies get a separate signature from OAuth state, so one kind of signed value cannot be reused as the other.

For local development, the file provides a ConsoleVerifier. It does not contact WorkOS. A developer can type an email into a simple local page, and email-code verification accepts one fixed code. This keeps the rest of the onboarding path realistic while avoiding outside credentials on a laptop.

If required WorkOS settings are missing in real mode, startup fails clearly instead of silently switching to insecure behavior.

#### Function details

##### `pack_state`  (lines 62–68)

```
def pack_state(carry: AuthCarry, secret: str) -> str
```

**Purpose**: Creates the OAuth state value that travels through the browser during sign-in. It stores the onboarding session and optional return target, then signs it so the gateway can later tell whether it was made here.

**Data flow**: It receives an AuthCarry with a session, optional conversation, and optional artifact, plus a secret key. It turns those values into JSON, encodes that JSON into browser-safe text, adds a signature, and returns one compact string for the sign-in redirect.

**Call relations**: When the sign-in flow needs to send a person to Google or the local dev sign-in page, this prepares the state value that will come back later. It relies on _state_signature to add the tamper-proof signature.

*Call graph*: calls 1 internal fn (_state_signature); 2 external calls (urlsafe_b64encode, dumps).


##### `unpack_state`  (lines 71–97)

```
def unpack_state(raw: str, secret: str) -> AuthCarry
```

**Purpose**: Reads back an OAuth state value and rejects it if it was forged or does not contain a usable session. It also cleans up optional return targets so a bad query string cannot force the user somewhere unexpected.

**Data flow**: It receives the raw state string from the browser and the secret key. It checks the signature, decodes the stored JSON, validates that the session is present and small enough, accepts only correctly shaped conversation IDs and artifact paths, and returns an AuthCarry. If the state is not trustworthy, it raises ValueError.

**Call relations**: This is the counterpart to pack_state in the callback path after sign-in. It calls _state_signature to compare the expected signature and then builds the AuthCarry object that the rest of onboarding can use.

*Call graph*: calls 1 internal fn (_state_signature); 4 external calls (__init__, urlsafe_b64decode, compare_digest, loads).


##### `_state_signature`  (lines 100–104)

```
def _state_signature(body: str, secret: str) -> str
```

**Purpose**: Makes the signature used for OAuth state values. The signature proves that the state text was produced by this gateway and was not changed in the browser.

**Data flow**: It receives the state body text and the gateway secret. It first derives a state-specific key from the secret, then signs the body with that key, and returns the signature as text.

**Call relations**: pack_state calls this when creating state, and unpack_state calls it when checking returned state. It is kept separate from cookie signing so state values and cookies do not share the exact same signing key.

*Call graph*: called by 2 (pack_state, unpack_state); 1 external calls (new).


##### `seal_session`  (lines 107–112)

```
def seal_session(session: str, secret: str) -> str
```

**Purpose**: Creates the signed onboarding session cookie value. This lets the browser hold a session name without being able to invent or alter one.

**Data flow**: It receives a session string and a secret. It signs the session with a cookie-specific signature and returns the session plus that signature joined into one cookie value.

**Call relations**: The onboarding flow uses this when minting or refreshing the browser cookie. It delegates the actual signing work to _cookie_signature.

*Call graph*: calls 1 internal fn (_cookie_signature).


##### `open_session`  (lines 115–124)

```
def open_session(value: str, secret: str) -> str | None
```

**Purpose**: Checks a signed onboarding session cookie and returns the session only if the signature is valid. A forged or damaged cookie is treated like no cookie at all.

**Data flow**: It receives the cookie value and the secret. It separates the session text from its signature, recomputes what the signature should be, compares them safely, and returns the session if they match. Otherwise it returns None.

**Call relations**: This is the read-side partner to seal_session. It calls _cookie_signature to verify the browser’s cookie before any claim or onboarding state is tied to that session.

*Call graph*: calls 1 internal fn (_cookie_signature); 1 external calls (compare_digest).


##### `_cookie_signature`  (lines 127–131)

```
def _cookie_signature(session: str, secret: str) -> str
```

**Purpose**: Makes the signature used for onboarding session cookies. It exists so cookie values can be trusted only if this gateway sealed them.

**Data flow**: It receives the session text and the gateway secret. It derives a cookie-specific signing key, signs the session, and returns the signature as text.

**Call relations**: seal_session uses it when creating cookies, and open_session uses it when checking cookies. It uses a different label than _state_signature so OAuth state and cookies are protected by separate derived keys.

*Call graph*: called by 2 (open_session, seal_session); 1 external calls (new).


##### `WorkosVerifier.authorization_url`  (lines 141–146)

```
def authorization_url(self, state: str) -> str
```

**Purpose**: Builds the URL for the real “Continue with Google” sign-in path. It asks WorkOS for a Google OAuth URL that will return to this gateway afterward.

**Data flow**: It receives a signed state string. It passes the Google provider, the configured redirect URI, and that state to the WorkOS client, and returns the URL the browser should visit.

**Call relations**: The real WorkOS verifier provides this during the browser sign-in start step. The verifier itself is normally created by workos_verifier_from_env.


##### `WorkosVerifier.exchange`  (lines 148–153)

```
async def exchange(self, code: str) -> str
```

**Purpose**: Turns a successful Google sign-in callback code into a verified email address. If WorkOS cannot complete the sign-in, it raises a user-facing verification error.

**Data flow**: It receives the short callback code returned after OAuth sign-in. It sends the code to WorkOS, reads the user email from the response, trims spaces, lowercases it, and returns that email. WorkOS failures become VerificationError with a simple sign-in failure message.

**Call relations**: This is used after the browser returns from the WorkOS or Google sign-in hop. It hands off the code to WorkOS and gives the onboarding flow the normalized email address to record.

*Call graph*: 1 external calls (__init__).


##### `WorkosVerifier.begin`  (lines 155–159)

```
async def begin(self, email: str) -> None
```

**Purpose**: Starts real email-code verification for a given address. It asks WorkOS to create a Magic Auth code, which WorkOS sends to the user.

**Data flow**: It receives an email address. It calls WorkOS to create the magic authentication challenge. It returns nothing on success; if WorkOS cannot send the code, it raises VerificationError with a message the user can understand.

**Call relations**: The onboarding flow calls this after it has accepted an email address and wants proof that the person controls it. It delegates delivery and code generation to WorkOS.

*Call graph*: 1 external calls (__init__).


##### `WorkosVerifier.confirm`  (lines 161–170)

```
async def confirm(self, email: str, code: str) -> bool
```

**Purpose**: Checks whether a user-entered email verification code is correct. Wrong, expired, or already-used codes are treated as a simple retryable “false,” while other WorkOS failures become sign-in errors.

**Data flow**: It receives an email and a code. It asks WorkOS to authenticate that pair. If WorkOS accepts it, the function returns true. If WorkOS says the grant is invalid, it returns false so the user can try another code. Other failures raise VerificationError.

**Call relations**: This completes the real email-code step begun by WorkosVerifier.begin. It interprets WorkOS’s errors so the rest of the onboarding flow can distinguish “try again” from “sign-in failed.”

*Call graph*: 1 external calls (__init__).


##### `ConsoleVerifier.authorization_url`  (lines 183–184)

```
def authorization_url(self, state: str) -> str
```

**Purpose**: Builds the local development sign-in URL instead of sending the browser to WorkOS or Google. It preserves the signed state so the normal callback flow is still exercised.

**Data flow**: It receives the signed state string. It URL-escapes that value so it is safe inside a query string, attaches it to the local console sign-in path, and returns the local URL.

**Call relations**: When workos_verifier_from_env selects console mode, the sign-in start step uses this method. It points the browser to console_signin_page rather than an outside provider.

*Call graph*: 1 external calls (quote).


##### `ConsoleVerifier.exchange`  (lines 186–187)

```
async def exchange(self, code: str) -> str
```

**Purpose**: In local development, treats the callback code field as the email address itself. This lets a developer sign in without contacting WorkOS.

**Data flow**: It receives the code value from the local form. It trims spaces, lowercases it, and returns it as the email address.

**Call relations**: This mirrors WorkosVerifier.exchange for console mode. The local sign-in page submits the typed email as the code, and this method turns it into the verified identity for the rest of onboarding.


##### `ConsoleVerifier.begin`  (lines 189–190)

```
async def begin(self, email: str) -> None
```

**Purpose**: Starts the local development email-code step by logging the fixed test code instead of sending an email. This keeps the flow visible without requiring mail delivery.

**Data flow**: It receives an email address. It writes a log message containing that email and the fixed console code, then returns nothing.

**Call relations**: This mirrors WorkosVerifier.begin for console mode. The surrounding onboarding flow can call the same begin method in both real and local modes.


##### `ConsoleVerifier.confirm`  (lines 192–193)

```
async def confirm(self, email: str, code: str) -> bool
```

**Purpose**: Checks the local development email code. It accepts only the fixed console code, so developers can complete the same flow without WorkOS.

**Data flow**: It receives an email and a code. It trims the code and compares it with the known console code, returning true for a match and false otherwise. The email is part of the shared interface but is not needed for the local check.

**Call relations**: This mirrors WorkosVerifier.confirm for console mode. It lets the rest of onboarding ask the same yes-or-no question no matter which verifier was selected.


##### `console_signin_page`  (lines 196–212)

```
def console_signin_page(state: str) -> str
```

**Purpose**: Builds the simple HTML page used for local development sign-in. The page asks for a work email and submits it to the same callback path used by real sign-in.

**Data flow**: It receives the signed state value. It escapes that value so it is safe inside HTML, places it in a hidden form field, and returns a complete HTML page as text.

**Call relations**: ConsoleVerifier.authorization_url points the browser to this local sign-in page. After the user submits the form, the normal callback route receives the state and the typed email.

*Call graph*: 1 external calls (escape).


##### `workos_console_mode`  (lines 215–216)

```
def workos_console_mode() -> bool
```

**Purpose**: Answers whether the gateway is configured for local console sign-in. Other code can use this to decide whether to expose the development-only sign-in page.

**Data flow**: It reads the WorkOS mode through _workos_mode, compares it with the console-mode name, and returns true or false.

**Call relations**: It is a small public check built on _workos_mode. It keeps mode parsing and validation in one place rather than repeating environment-variable logic elsewhere.

*Call graph*: calls 1 internal fn (_workos_mode).


##### `workos_verifier_from_env`  (lines 219–233)

```
def workos_verifier_from_env() -> WorkosVerifier | ConsoleVerifier
```

**Purpose**: Creates the verifier object the gateway should use for sign-in. It chooses either the real WorkOS verifier or the local console verifier based on environment variables.

**Data flow**: It reads and validates the configured mode. In console mode, it logs a warning and returns a ConsoleVerifier. In real WorkOS mode, it requires the API key, client ID, and redirect URI, creates an async WorkOS client, and returns a WorkosVerifier.

**Call relations**: This is the setup point for the rest of the sign-in system. It calls _workos_mode to choose the path and _require_env to fail early if real WorkOS credentials are missing.

*Call graph*: calls 2 internal fn (_require_env, _workos_mode); 3 external calls (__init__, __init__, AsyncWorkOSClient).


##### `_workos_mode`  (lines 236–240)

```
def _workos_mode() -> str
```

**Purpose**: Reads the configured WorkOS mode and rejects unknown values. This prevents accidental or misspelled modes from silently changing authentication behavior.

**Data flow**: It reads WORKOS_MODE from the process environment, defaulting to real WorkOS mode. It trims and lowercases the value, checks that it is either real mode or console mode, and returns the valid mode. If not, it raises RuntimeError.

**Call relations**: workos_console_mode and workos_verifier_from_env both call this so mode handling is consistent. It is the single gatekeeper for deciding whether local development authentication is allowed.

*Call graph*: called by 2 (workos_console_mode, workos_verifier_from_env).


##### `_require_env`  (lines 243–247)

```
def _require_env(name: str) -> str
```

**Purpose**: Reads a required environment variable and fails clearly if it is missing. This keeps real WorkOS sign-in from starting with incomplete credentials.

**Data flow**: It receives the environment variable name. It looks up the value in the process environment and returns it if present. If the value is missing or empty, it raises RuntimeError with a message naming the missing setting.

**Call relations**: workos_verifier_from_env uses this when building a real WorkOS verifier. That means missing API key, client ID, or redirect URI problems are caught during setup rather than during a user’s sign-in attempt.

*Call graph*: called by 1 (workos_verifier_from_env).


### Workspace enrollment
Verified users are matched to or placed into workspaces, issued gateway tokens, and can trigger first-run workspace setup.

### `control/src/ufo_control/gateway_shared.py`

`domain_logic` · `hosted onboarding / request handling`

Hosted onboarding needs to answer a simple but important question: “Now that this person has proved they own this email address, which shared workspace should they enter?” This file provides that answer. It treats an email domain, such as the part after “@”, as a possible shared-fleet workspace identity. It also respects existing direct memberships, so a person can be offered workspaces they already belong to.

The main class, SharedWorkspaces, uses a database connection pool to look up choices and to safely create or join workspaces. Its choices method gathers two kinds of options: workspaces where the exact email is already a member, and the one workspace associated with the verified domain. If two workspaces seem to claim the same domain, it stops with an error rather than guessing.

When creating or joining, the file uses a deterministic workspace ID for a domain. That means the same domain always points to the same UUID, like using the same street address to find the same building. The private _ensure method is the central safety gate: it creates the workspace if needed, checks that the first member’s email domain still matches, adds the new member, and creates a default agent for the workspace. The first member becomes an admin; later members do not by default.

#### Function details

##### `serve_dsn`  (lines 20–27)

```
def serve_dsn() -> str
```

**Purpose**: This reads the database connection string used by hosted onboarding. It fails loudly if the needed environment variable is missing, because onboarding must write workspace data using the correct database role.

**Data flow**: It reads the environment variable named UFO_CONTROL_SERVE_DSN. If a value is present, it returns that text. If it is absent or empty, it raises an error explaining that hosted onboarding cannot safely write the workspace row without it.

**Call relations**: This is a small setup helper for code that needs the hosted onboarding database connection. It does not call other project functions; it simply checks the process environment before later database work can begin.


##### `SharedWorkspaces.choices`  (lines 53–104)

```
async def choices(self, domain: str, email: str) -> tuple[WorkspaceChoice, ...]
```

**Purpose**: This finds every workspace a verified email address is allowed to enter. It combines exact membership matches with the workspace tied to the verified email domain.

**Data flow**: It receives a domain and an email address. It normalizes them to lowercase, derives a stable UUID from the domain, and asks the database for matching workspaces. It then checks for impossible domain conflicts, builds readable labels, marks whether the email is already a member, and returns a tuple of WorkspaceChoice objects.

**Call relations**: This is usually the first step after email verification, when onboarding needs to show the user where they can go. It uses uuid5 to turn the domain into the same workspace ID every time, then creates WorkspaceChoice records that later feed into join if the user selects one.

*Call graph*: 2 external calls (__init__, uuid5).


##### `SharedWorkspaces.create`  (lines 106–108)

```
async def create(self, domain: str, email: str) -> EnsuredWorkspace
```

**Purpose**: This creates, or confirms, the shared workspace for a verified domain and seats the email address in it. It is the path used when onboarding should make the domain’s workspace available.

**Data flow**: It receives a domain and email address. It lowers the domain, turns it into a deterministic UUID, and passes that workspace ID along with the original domain and email to _ensure. It returns the EnsuredWorkspace result from that deeper safety check.

**Call relations**: This is a thin public doorway into the main creation flow. It calls _ensure, which performs the actual database work: creating the workspace if necessary, adding the member, and creating the default agent.

*Call graph*: calls 1 internal fn (_ensure); 1 external calls (uuid5).


##### `SharedWorkspaces.join`  (lines 110–127)

```
async def join(self, choice: WorkspaceChoice, domain: str, email: str) -> EnsuredWorkspace
```

**Purpose**: This adds a verified email address to one of the workspace choices, or confirms their existing membership. It also reports whether the person is an admin, so later onboarding can decide what options to show.

**Data flow**: It receives a WorkspaceChoice plus the verified domain and email. If the choice is not already an exact membership, it asks _ensure to add the person to that workspace. If the person is already a member, it opens the workspace context, reads their admin flag from the database, and returns an EnsuredWorkspace. If the membership disappeared since choices were shown, it raises an error.

**Call relations**: This follows after choices, when a user has picked a workspace. For non-member domain-based choices, it hands off to _ensure to safely create or join. For already-member choices, it uses the workspace context and a workspace transaction to verify the current database state before returning.

*Call graph*: calls 1 internal fn (_ensure); 4 external calls (__init__, select, workspace_tx, ws).


##### `SharedWorkspaces._ensure`  (lines 129–180)

```
async def _ensure(self, workspace_id: UUID, domain: str, email: str) -> EnsuredWorkspace
```

**Purpose**: This is the core safety routine that makes sure a workspace exists, belongs to the expected domain, has the verified person as a member, and has a default agent. It is private because callers should use create or join instead of assembling this sequence themselves.

**Data flow**: It receives a workspace UUID, a domain, and an email address. It normalizes the email, enters the workspace context, starts a database transaction, inserts the workspace if it does not exist, locks the workspace row so two onboarding attempts do not race each other, checks the first member’s domain, creates the member, inserts the default agent if needed, reads whether the member is an admin, and returns an EnsuredWorkspace with the workspace ID and admin flag.

**Call relations**: Both create and join rely on this function when they need to create or seat someone in a workspace. Inside, it coordinates lower-level helpers: workspace_tx provides the database transaction, ws sets the active workspace context, create_member adds the person, email_domain checks ownership rules, and database insert/select calls update the workspace, member, and agent tables.

*Call graph*: called by 2 (create, join); 8 external calls (__init__, insert, select, workspace_tx, create_member, email_domain, ws, uuid4).


### `control/src/ufo_control/gateway_token.py`

`domain_logic` · `token issuance`

This file is a small bridge between the control service and the shared token system. A bearer token is like a temporary badge: the client stores it, and later services can check it to decide whether the user is allowed in. Here, the badge is meant for a hosted member and is stored by the client in `~/.ufo/credentials`.

The important choice made in this file is the token’s lifetime: `TOKEN_TTL` is set to 30 days. It also names the environment variable, `UFO_TOKEN_SECRET`, where the signing secret is expected to come from elsewhere. A signing secret is a private value used to prove that the token really came from this system and was not forged.

The file does not build the token format itself. Instead, it calls the shared `ufo.bearer.mint_token` function. That matters because both token creation and token checking need to agree on the exact shape of the signed data. By using the shared codec, this file avoids a common security and compatibility problem: one part of the system creating tokens that another part cannot verify.

#### Function details

##### `mint_token`  (lines 13–14)

```
def mint_token(secret: str, workspace_id: str, email: str, now: datetime | None=None) -> str
```

**Purpose**: Creates a signed bearer token for a workspace member, valid for 30 days. Someone would use this when the gateway needs to give a client a credential it can store and present later.

**Data flow**: It receives a secret, a workspace ID, an email address, and optionally the current time. It combines those with the fixed 30-day lifetime and passes them to the shared bearer-token maker. The result is a token string that can later be verified by code that understands the same shared token format.

**Call relations**: This function is the control-service-facing wrapper around `ufo.bearer.mint_token`. When token creation is needed, callers use this local function so they do not have to remember the correct lifetime. It then hands off to the shared bearer-token code, which does the actual signed token construction.

*Call graph*: 1 external calls (mint_token).


### `core/src/ufo/onboarding.py`

`orchestration` · `startup / first-run initialization`

This file is the “opening checklist” for a brand-new UFO installation. Without it, the system would not have a workspace to use, an admin user to own it, or a default assistant agent to answer with. It also protects against accidentally running setup twice, which could create duplicate starting data.

The flow starts by checking that the chosen AI model has the environment variable it needs, such as an API key. It also checks whether extension onboarding steps need a credential store. These checks happen before writing to the database, so a failed setup does not leave behind a half-created workspace.

Next, the file opens a database transaction, which is like putting all database changes in one sealed envelope: either the whole initial workspace is created, or none of it is. It refuses to continue if any member already exists, treating that as proof that the workspace was already initialized. Then it creates a workspace, creates the initial admin member, and creates the main agent with a default prompt.

After the core setup succeeds, each installed extension may run its own onboarding steps. Each extension gets a scoped context, meaning it only sees the credential slots it declared. If one extension step fails, the error is logged and the next step continues, so add-ons cannot break the core workspace setup.

#### Function details

##### `run_onboarding_steps`  (lines 46–74)

```
async def run_onboarding_steps(manifests: tuple[Manifest, ...], workspace_id: UUID, credentials: CredentialStore | None) -> None
```

**Purpose**: Runs the first-time setup steps supplied by installed extensions after the main workspace already exists. It keeps extension failures contained, so one broken add-on does not stop other add-ons or undo the core setup.

**Data flow**: It receives a list of extension manifests, the new workspace ID, and an optional credential store. It enters that workspace’s context, checks each extension for onboarding steps, builds that extension’s limited context when credentials are available, and calls each step. If a step raises an error, it records a log message and continues; it does not return a value.

**Call relations**: This is called by Onboarding.run_steps after the workspace and admin have been created. During the extension loop, it asks context_for for the extension-specific context, uses ws to mark which workspace the work belongs to, and uses log to record skipped or failed extension setup.

*Call graph*: called by 1 (run_steps); 3 external calls (context_for, log, ws).


##### `Onboarding.run`  (lines 89–92)

```
async def run(self) -> Onboarded
```

**Purpose**: Runs the full first-run onboarding flow from start to finish. It first creates the core workspace data, then runs optional extension setup.

**Data flow**: It starts with the Onboarding object’s stored configuration, email, model name, credentials, and extension manifests. It calls create to produce an Onboarded result containing the new workspace and member IDs, then passes that result into run_steps. It returns the same Onboarded result to the caller.

**Call relations**: This is the high-level method a caller uses when it wants the whole initialization process. It delegates the durable core creation to Onboarding.create and the add-on setup phase to Onboarding.run_steps.

*Call graph*: calls 2 internal fn (create, run_steps).


##### `Onboarding.create`  (lines 94–100)

```
async def create(self) -> Onboarded
```

**Purpose**: Creates only the core, durable starting state: required key checks, workspace, first admin member, and main agent. It deliberately does this before extension setup, so the CLI can bind to a valid workspace even if an add-on later fails.

**Data flow**: It reads the selected model, configuration, credentials, manifests, and admin email from the Onboarding object. It first checks that the model key exists when needed, then checks that credentials are available if extension steps require them. If those checks pass, it creates the workspace records and returns an Onboarded object with the new IDs.

**Call relations**: This is called by Onboarding.run as the first phase. It calls _require_model_key and _require_credentials_for_steps for early safety checks, then calls _create_workspace to write the initial database records.

*Call graph*: calls 3 internal fn (_create_workspace, _require_credentials_for_steps, _require_model_key); called by 1 (run).


##### `Onboarding.run_steps`  (lines 102–103)

```
async def run_steps(self, onboarded: Onboarded) -> None
```

**Purpose**: Runs extension onboarding for an already-created workspace. It is a small bridge between the main Onboarding object and the standalone extension-step runner.

**Data flow**: It receives an Onboarded object, reads its workspace ID, and combines that with the Onboarding object’s manifests and credentials. It passes those values to run_onboarding_steps and returns nothing.

**Call relations**: This is called by Onboarding.run after create succeeds. It hands control to run_onboarding_steps, which performs the actual per-extension setup work.

*Call graph*: calls 1 internal fn (run_onboarding_steps); called by 1 (run).


##### `Onboarding._require_credentials_for_steps`  (lines 105–116)

```
def _require_credentials_for_steps(self) -> None
```

**Purpose**: Stops initialization early if installed extensions have onboarding steps but no credential store is available. This avoids creating a workspace that extension setup cannot safely finish.

**Data flow**: It looks at the Onboarding object’s credential store and extension manifests. If credentials exist, it allows setup to continue. If credentials are missing and any manifest has onboarding steps, it raises an error explaining which environment variable must be set; otherwise it returns normally.

**Call relations**: This is called by Onboarding.create before any database records are created. It does not call other project functions; it acts as a guardrail before _create_workspace can run.

*Call graph*: called by 1 (create).


##### `Onboarding._require_model_key`  (lines 118–125)

```
def _require_model_key(self) -> None
```

**Purpose**: Checks that the selected model has the environment variable it needs before the first assistant turn can happen. This catches a missing model API key during setup instead of later during use.

**Data flow**: It asks _model_key_env which environment variable, if any, is required for the configured model. If no variable is required, it does nothing. If a variable is required but not present in the process environment, it raises an error; otherwise setup continues.

**Call relations**: This is called by Onboarding.create before database creation. It relies on _model_key_env to identify the needed environment variable, then reads the operating system environment directly.

*Call graph*: calls 1 internal fn (_model_key_env); called by 1 (create).


##### `Onboarding._model_key_env`  (lines 127–130)

```
def _model_key_env(self) -> str | None
```

**Purpose**: Finds the name of the environment variable that should contain the key for the selected model, if the core system knows one. Some extension-provided models resolve their own keys later, so this may return nothing.

**Data flow**: It reads the Onboarding object’s configuration, installed manifests, and selected model name. It builds the model registry and asks it for the key environment variable for that model. It returns the variable name as text, or None if there is no eager check to perform.

**Call relations**: This is called by _require_model_key. It hands the model lookup to model_registry, which knows about both built-in model providers and extension-contributed ones.

*Call graph*: called by 1 (_require_model_key); 1 external calls (model_registry).


##### `Onboarding._create_workspace`  (lines 132–156)

```
async def _create_workspace(self) -> Onboarded
```

**Purpose**: Writes the initial workspace, admin member, and main agent to the database exactly once. It refuses to run if the database already has a member, which signals that onboarding has already happened.

**Data flow**: It opens a workspace database transaction, looks for an existing member email, and raises AlreadyInitialized if one is found. If the database is empty, it creates fresh IDs, inserts a workspace row, calls create_member to add the initial admin, inserts the main agent row with the default prompt and chosen model, and returns an Onboarded object with the workspace and member IDs.

**Call relations**: This is called by Onboarding.create after the early key and credential checks pass. It uses workspace_tx for the database boundary, SQLAlchemy insert and select helpers for database statements, uuid4 for new IDs, create_member for the first admin, and returns the result through Onboarded.

*Call graph*: called by 1 (create); 7 external calls (__init__, __init__, insert, select, workspace_tx, create_member, uuid4).


### Operator Slack invitation
Completed signup claims can be converted into one-time Slack Connect invitations to UFO’s operator workspace.

### `control/src/ufo_control/gateway_slack_connect.py`

`orchestration` · `startup and background polling after completed signup claims`

This file solves a practical onboarding problem: after a customer signs up and creates a workspace, UFO wants a shared Slack channel with them, but signup itself must stay fast and reliable. Instead of making the user wait for Slack, the completed signup claim becomes the durable “to-do item” in the database. A background poller repeatedly looks for eligible claims, creates or recovers one delivery row, leases one row so only one gateway replica works on it, and then talks to Slack.

The workflow is careful about duplicate invitations. The Slack channel name is deterministic, meaning every replica derives the same name from the customer email domain. That channel name is the safety boundary: if Slack says the name already exists, the code searches for the existing channel rather than blindly creating another. Before inviting, the code records that an invite is being attempted. If the network drops after Slack receives the invite, a later run tries to reconcile by checking Slack’s outgoing invitations and channel sharing state instead of sending a second invite.

Temporary Slack problems, such as rate limits or server errors, are rescheduled with backoff. Permanent problems, such as bad configuration or an invalid recipient, mark the row failed for human review. The poller keeps running even when one row fails, so later customers are not stranded.

#### Function details

##### `SlackTransientError.__init__`  (lines 167–169)

```
def __init__(self, message: str, retry_after: float | None=None) -> None
```

**Purpose**: This creates an error object for Slack problems that might clear up later, such as rate limiting, timeouts, or temporary server trouble. It can also carry Slack’s suggested wait time before retrying.

**Data flow**: It receives a human-readable message and, optionally, a retry-after delay. It stores the message as the exception text and saves the delay on the error object for later scheduling.

**Call relations**: SlackConnectClient._call raises this when an HTTP request to Slack fails in a way that should be retried. Later, SlackConnectInviter._advance catches this kind of error and hands it to the retry scheduler.

*Call graph*: called by 1 (_call).


##### `rearm_failed_delivery`  (lines 188–203)

```
async def rearm_failed_delivery(pool: asyncpg.Pool, onboard_claim_id: UUID) -> datetime | None
```

**Purpose**: This is the operator recovery hook for trying one failed Slack Connect delivery again after the underlying problem has been fixed. It deliberately does not call Slack; it only changes the database row back into a retryable state.

**Data flow**: It receives a database pool and the signup claim ID. It looks for a matching row that is currently failed, resets its state, clears old worker and error fields, and returns the time when the row was last changed; if there was no failed row to re-arm, it returns nothing.

**Call relations**: This function is meant to be called by an operator-facing command or recovery path. It uses the database directly through asyncpg.Pool.fetchval and leaves the normal background poller to perform the actual Slack work later.

*Call graph*: 1 external calls (fetchval).


##### `SlackConnectClient.team_id`  (lines 215–216)

```
async def team_id(self) -> str
```

**Purpose**: This asks Slack which workspace the configured bot token belongs to. It is used as a safety check before creating channels or sending invitations.

**Data flow**: It sends an auth.test request to Slack, receives Slack’s response, and extracts the team_id text field. The output is the Slack workspace ID associated with the bot token.

**Call relations**: SlackConnectInviter._verify_team calls this during each delivery attempt. Internally it uses SlackConnectClient._call to make the Slack request and SlackConnectClient._text to pull the needed value out of Slack’s response.

*Call graph*: calls 2 internal fn (_call, _text).


##### `SlackConnectClient.create_channel`  (lines 218–220)

```
async def create_channel(self, name: str) -> str
```

**Purpose**: This creates a public Slack channel with the deterministic customer channel name. The result is the Slack channel ID that later steps need in order to invite the customer.

**Data flow**: It receives a channel name, sends it to Slack’s conversations.create method, and extracts the new channel’s ID from Slack’s response. If Slack rejects the request, the shared Slack call wrapper turns that into an appropriate error.

**Call relations**: SlackConnectInviter._open_channel uses this when a delivery row does not already have a channel ID. The function delegates the HTTP work to SlackConnectClient._call and response reading to SlackConnectClient._text.

*Call graph*: calls 2 internal fn (_call, _text).


##### `SlackConnectClient.channel_id_by_name`  (lines 222–243)

```
async def channel_id_by_name(self, name: str) -> str
```

**Purpose**: This finds the Slack ID for an already-existing channel with the exact expected name. It is mainly the recovery path when Slack says the desired channel name is already taken.

**Data flow**: It receives a channel name, pages through Slack’s public channel list, including archived channels, and compares each channel’s name. If it finds an exact match, it returns that channel’s ID; if it reaches the configured search limit without a match, it raises a permanent error.

**Call relations**: SlackConnectInviter._open_channel calls this after create_channel reports that the name is already taken. It uses SlackConnectClient._call for each Slack page and SlackConnectClient._text to safely extract the ID.

*Call graph*: calls 2 internal fn (_call, _text); 1 external calls (__init__).


##### `SlackConnectClient.outgoing_invite_id`  (lines 245–276)

```
async def outgoing_invite_id(self, channel_id: str) -> str | None
```

**Purpose**: This checks whether Slack already has a live Slack Connect invitation for a channel. It helps avoid sending a duplicate invite after an earlier attempt may have reached Slack but the local process lost the response.

**Data flow**: It receives a Slack channel ID, pages through Slack’s outgoing Slack Connect invitations, and looks for entries for that channel. If it finds a live invite, it returns the invitation ID; if it finds only dead invites, it returns nothing; if Slack shows an unknown status or the search limit is exceeded, it raises a permanent error for human review.

**Call relations**: SlackConnectInviter._invite calls this when the database says an invitation was previously attempted. It uses SlackConnectClient._call to query Slack and SlackConnectClient._text to read the invite ID from a matching response.

*Call graph*: calls 2 internal fn (_call, _text); 1 external calls (__init__).


##### `SlackConnectClient.is_externally_shared`  (lines 278–281)

```
async def is_externally_shared(self, channel_id: str) -> bool
```

**Purpose**: This asks Slack whether a channel is already shared, or in the process of being shared, with an outside workspace. That can prove the invite worked even if Slack no longer lists the invite itself.

**Data flow**: It receives a Slack channel ID, calls Slack’s conversations.info method, reads the returned channel details, and returns true if Slack marks the channel as externally shared or pending external sharing.

**Call relations**: SlackConnectInviter._invite uses this as a second reconciliation check after looking for a live outgoing invitation. It relies on SlackConnectClient._call for the Slack request.

*Call graph*: calls 1 internal fn (_call).


##### `SlackConnectClient.invite_shared`  (lines 283–290)

```
async def invite_shared(self, channel_id: str, email: str) -> str
```

**Purpose**: This sends the actual Slack Connect invitation to the customer’s email address. It is the step that asks Slack to connect the operator channel with the customer.

**Data flow**: It receives a channel ID and an email address. It first rejects emails longer than the maximum allowed length, then calls Slack’s conversations.inviteShared method, and returns Slack’s invitation ID from the response.

**Call relations**: SlackConnectInviter._invite calls this after it has decided that there is no existing live invite to reuse. It uses SlackConnectClient._call to talk to Slack and SlackConnectClient._text to extract the invite ID.

*Call graph*: calls 2 internal fn (_call, _text); 1 external calls (__init__).


##### `SlackConnectClient.redact`  (lines 292–293)

```
def redact(self, message: str) -> str
```

**Purpose**: This removes the bot token from error messages before they are stored or logged. It protects a secret from accidentally appearing in logs, database rows, or tracebacks.

**Data flow**: It receives a message string, replaces any copy of the bot token with a fixed placeholder, trims the result to the configured maximum length, and returns the safe text.

**Call relations**: The inviter’s failure and retry paths use this before writing error text or logging it. It is a small safety tool used around Slack-related errors.


##### `SlackConnectClient._call`  (lines 295–323)

```
async def _call(self, method: str, params: dict[str, str | int]) -> dict[str, Any]
```

**Purpose**: This is the shared low-level Slack Web API caller. It sends one HTTP request, interprets Slack and HTTP failures, and turns them into errors the rest of the workflow can understand.

**Data flow**: It receives a Slack method name and request parameters. It posts them to Slack with the bot token, omits empty parameters, parses the JSON response, and returns the response payload when Slack says ok. Network problems, rate limits, server errors, name conflicts, temporary Slack errors, and permanent Slack errors are converted into specific exceptions.

**Call relations**: All higher-level SlackConnectClient methods call this instead of making HTTP requests themselves. When Slack reports rate limiting, it calls _retry_after to preserve Slack’s suggested delay; otherwise it raises transient or terminal errors that SlackConnectInviter._advance later reacts to.

*Call graph*: calls 2 internal fn (__init__, _retry_after); called by 6 (channel_id_by_name, create_channel, invite_shared, is_externally_shared, outgoing_invite_id, team_id); 3 external calls (__init__, __init__, AsyncClient).


##### `SlackConnectClient._text`  (lines 325–331)

```
def _text(self, payload: dict[str, Any], *path: str) -> str
```

**Purpose**: This safely extracts a text value from a nested Slack response. It prevents later code from silently continuing with a malformed or unexpected Slack response.

**Data flow**: It receives a response dictionary and a path of keys, then walks through the dictionary one key at a time. If any key is missing or the structure is not a dictionary where expected, it raises a permanent error; otherwise it returns the found value as text.

**Call relations**: The client methods that need IDs or team information use this after SlackConnectClient._call returns. It centralizes response checking so each public Slack operation does not have to repeat the same validation.

*Call graph*: called by 5 (channel_id_by_name, create_channel, invite_shared, outgoing_invite_id, team_id); 1 external calls (__init__).


##### `_retry_after`  (lines 334–338)

```
def _retry_after(response: httpx.Response) -> float | None
```

**Purpose**: This reads Slack’s retry-after header when Slack rate limits the app. It caps the delay so one response cannot pause retries for an excessive time.

**Data flow**: It receives an HTTP response, checks the retry-after header, and returns a bounded number of seconds if the header is a valid whole number. If the header is missing or unusable, it returns nothing.

**Call relations**: SlackConnectClient._call uses this when Slack returns HTTP 429, the standard “too many requests” response. The resulting delay is stored on SlackTransientError so the inviter can schedule the next attempt sensibly.

*Call graph*: called by 1 (_call).


##### `SlackConnectInviter.run`  (lines 364–388)

```
async def run(self) -> None
```

**Purpose**: This is the never-ending background loop for Slack Connect delivery. It keeps sweeping for work, and it deliberately survives row-level failures so future customers still get processed.

**Data flow**: It starts with no claimed row, repeatedly calls poll, and tracks consecutive unexpected sweep failures for logging. If poll reports no work, it sleeps for the configured interval; if the task is cancelled, it exits by re-raising the cancellation.

**Call relations**: The gateway lifespan starts this method when Slack Connect delivery is enabled. It calls SlackConnectInviter.poll for each sweep and uses asyncio.sleep only when there was no row to process or a sweep failed.

*Call graph*: calls 1 internal fn (poll); 1 external calls (sleep).


##### `SlackConnectInviter.poll`  (lines 390–405)

```
async def poll(self) -> bool
```

**Purpose**: This performs one sweep of the delivery system. It first creates database delivery rows for newly eligible signups, then claims at most one due row and advances it through Slack work.

**Data flow**: It materializes pending deliveries from completed signup claims, tries to claim one due delivery row, and returns false if none exists. If it claims a row, it starts a background lease-renewal task, advances the delivery, cancels the renewal task, waits for cleanup, and returns true.

**Call relations**: SlackConnectInviter.run calls this over and over. Within one sweep it calls _materialize, _claim, starts _renew_lease with asyncio.create_task, calls _advance, and uses asyncio.gather during cleanup.

*Call graph*: calls 4 internal fn (_advance, _claim, _materialize, _renew_lease); called by 1 (run); 2 external calls (create_task, gather).


##### `SlackConnectInviter._materialize`  (lines 407–421)

```
async def _materialize(self) -> None
```

**Purpose**: This turns completed signup claims into Slack Connect delivery rows in the database. It is the bridge from “a customer completed signup” to “there is work for the Slack invitation poller.”

**Data flow**: It opens a database transaction, takes a PostgreSQL advisory lock, and runs one insert-from-select statement that creates pending delivery rows for eligible claims. Duplicate claim IDs or duplicate channel names are skipped rather than aborting the whole batch.

**Call relations**: SlackConnectInviter.poll calls this before looking for a row to claim. The advisory lock and conflict handling make it safe for multiple gateway replicas to run the same poller at the same time.

*Call graph*: called by 1 (poll).


##### `SlackConnectInviter._claim`  (lines 423–435)

```
async def _claim(self) -> _Delivery | None
```

**Purpose**: This leases one pending or expired delivery row for the current worker. A lease is like putting a temporary “I’m working on this” sticker on the row so another replica does not do the same job at the same time.

**Data flow**: It runs the claim SQL with the worker ID and lease duration. If no row is ready, it returns nothing; otherwise it converts the database row into a _Delivery object containing the claim ID, email, channel details, previous invitation details, and attempt count.

**Call relations**: SlackConnectInviter.poll calls this after materializing work. The returned _Delivery is passed into _advance, while the claim ID is also used to start lease renewal.

*Call graph*: called by 1 (poll); 1 external calls (__init__).


##### `SlackConnectInviter._renew_lease`  (lines 437–459)

```
async def _renew_lease(self, onboard_claim_id: UUID) -> None
```

**Purpose**: This keeps the worker’s lease alive while Slack calls are in progress. It reduces the chance that another gateway replica will pick up the same row during a slow network request and send a duplicate invitation.

**Data flow**: It receives a signup claim ID, sleeps for the renewal interval, and then extends the row’s claim expiration if the row still belongs to this worker. Database renewal failures are logged and the loop tries again; cancellation stops the loop.

**Call relations**: SlackConnectInviter.poll starts this as a background task while _advance is working. The writeback steps in the delivery path still check ownership separately, so if the lease is lost, the main path notices through _write.

*Call graph*: called by 1 (poll); 1 external calls (sleep).


##### `SlackConnectInviter._advance`  (lines 461–488)

```
async def _advance(self, delivery: _Delivery) -> None
```

**Purpose**: This is the main state machine for one claimed delivery. It verifies the Slack token, opens or recovers the channel, sends or reconciles the invite, and finally marks the row delivered.

**Data flow**: It receives a _Delivery record. It verifies the Slack team, obtains a channel ID, obtains or sends an invitation, and writes the delivered state on success. Temporary Slack errors are rescheduled, permanent errors are marked failed, lost leases are allowed to escape to the caller, and unexpected errors are logged then marked failed.

**Call relations**: SlackConnectInviter.poll calls this after claiming a row. It coordinates _verify_team, _open_channel, _invite, _reschedule, _fail, and _write, which are the smaller steps of the delivery machine.

*Call graph*: calls 6 internal fn (_fail, _invite, _open_channel, _reschedule, _verify_team, _write); called by 1 (poll).


##### `SlackConnectInviter._verify_team`  (lines 490–498)

```
async def _verify_team(self) -> None
```

**Purpose**: This proves that the configured Slack bot token belongs to the expected operator workspace before mutating anything. It protects against accidentally creating channels or invites in the wrong Slack workspace.

**Data flow**: It asks Slack for the bot token’s team ID and compares it with the configured expected team ID. If they differ, it raises a configuration error; if they match, it returns normally and delivery can continue.

**Call relations**: SlackConnectInviter._advance calls this at the start of every delivery attempt. It intentionally re-checks every time rather than trusting a cached answer, so token rotation mistakes are caught quickly.

*Call graph*: called by 1 (_advance); 1 external calls (__init__).


##### `SlackConnectInviter._open_channel`  (lines 500–506)

```
async def _open_channel(self, delivery: _Delivery) -> str
```

**Purpose**: This creates the customer’s operator-side Slack channel, or recovers its ID if it already exists. It records the channel ID in the database so later attempts can continue from that point.

**Data flow**: It receives a delivery record and asks Slack to create the deterministic channel name. If Slack says the name is taken, it searches for the existing channel by name. It then writes the channel ID to the delivery row and returns that ID.

**Call relations**: SlackConnectInviter._advance calls this when the claimed row does not already have a channel ID. It uses SlackConnectInviter._write for the database update so the write only succeeds while this worker still owns the lease.

*Call graph*: calls 1 internal fn (_write); called by 1 (_advance).


##### `SlackConnectInviter._invite`  (lines 508–525)

```
async def _invite(self, delivery: _Delivery, channel_id: str) -> str | None
```

**Purpose**: This sends the Slack Connect invite, but first tries to recover from any earlier invite attempt that may already have reached Slack. That is the main protection against duplicate invitations.

**Data flow**: It receives a delivery record and channel ID. If an invite was previously attempted, it checks for a live outgoing invite and then checks whether the channel is already externally shared. If neither proves success, it records a fresh invite attempt time, sends the invite to the delivery email, stores the invitation ID, and returns it; if the channel is already shared, it may return nothing because there is no invite ID to store.

**Call relations**: SlackConnectInviter._advance calls this after a channel ID is available. It calls _persist_invitation when Slack provides an invitation ID and _write when it needs to record that an invite attempt is starting.

*Call graph*: calls 2 internal fn (_persist_invitation, _write); called by 1 (_advance).


##### `SlackConnectInviter._persist_invitation`  (lines 527–529)

```
async def _persist_invitation(self, delivery: _Delivery, invitation_id: str) -> str
```

**Purpose**: This saves Slack’s invitation ID on the delivery row. It makes later retries or audits know exactly which Slack invitation was created.

**Data flow**: It receives a delivery record and invitation ID, writes the invitation ID into the database row, and returns the same ID for the caller to keep using.

**Call relations**: SlackConnectInviter._invite calls this after finding an existing live invite or after successfully creating a new one. It uses _write so the save is rejected if another worker has taken over the row.

*Call graph*: calls 1 internal fn (_write); called by 1 (_invite).


##### `SlackConnectInviter._reschedule`  (lines 531–550)

```
async def _reschedule(self, delivery: _Delivery, error: SlackTransientError) -> None
```

**Purpose**: This handles temporary Slack failures by putting the row back into the pending queue for a later retry. If the row has already tried too many times, it turns the problem into a failed delivery for review.

**Data flow**: It receives the delivery record and a transient error. It checks the attempt count, chooses a delay from Slack’s retry-after value or an exponential backoff schedule, writes the row back to pending with the next attempt time and redacted error text, and logs the retry.

**Call relations**: SlackConnectInviter._advance calls this when SlackConnectClient reports a retryable problem. If the maximum attempt count is reached, this function calls _fail instead of scheduling another try; otherwise it writes the retry state through _write.

*Call graph*: calls 2 internal fn (_fail, _write); called by 1 (_advance); 1 external calls (timedelta).


##### `SlackConnectInviter._fail`  (lines 552–563)

```
async def _fail(self, delivery: _Delivery, error: Exception) -> None
```

**Purpose**: This marks a delivery as failed so an operator can inspect and fix it. It is used for permanent Slack errors, unexpected exceptions, and exhausted retry attempts.

**Data flow**: It receives a delivery record and an error, redacts any bot token from the error text, writes the row into the failed state with no active worker or next retry time, and logs the failure.

**Call relations**: SlackConnectInviter._advance calls this for terminal and unexpected errors. SlackConnectInviter._reschedule also calls it when a transient problem has exceeded the allowed number of attempts.

*Call graph*: calls 1 internal fn (_write); called by 2 (_advance, _reschedule).


##### `SlackConnectInviter._write`  (lines 565–574)

```
async def _write(self, onboard_claim_id: UUID, assignment: str, *values: object) -> None
```

**Purpose**: This is the guarded database writer for a claimed delivery row. It makes sure only the worker that currently owns the lease can update the row.

**Data flow**: It receives a claim ID, a SQL assignment fragment, and optional values for that assignment. It updates the row only if the worker ID still matches this inviter’s worker ID; if the update affects no row, it raises a lease-lost error.

**Call relations**: All delivery state changes go through this function: opening channels, recording invite attempts, saving invitation IDs, rescheduling, failing, and marking delivered. SlackConnectInviter._advance and its helper steps rely on it to avoid stale workers overwriting newer work.

*Call graph*: called by 6 (_advance, _fail, _invite, _open_channel, _persist_invitation, _reschedule); 1 external calls (__init__).


##### `slack_connect_from_env`  (lines 577–592)

```
def slack_connect_from_env(pool: asyncpg.Pool) -> SlackConnectInviter | None
```

**Purpose**: This builds the Slack Connect background inviter from environment variables, or returns nothing when the feature is disabled. It makes startup fail clearly if the feature is enabled but required settings are missing or malformed.

**Data flow**: It reads the enable switch from the environment. If disabled, it returns None. If enabled, it requires the bot token and expected Slack team ID, creates a SlackConnectClient, creates a SlackConnectInviter with a worker ID based on hostname and process ID, and returns it; if the switch is not a true/false value, it raises an error.

**Call relations**: Gateway startup uses this to decide whether to run Slack Connect invitation delivery. It calls _require_env for required settings and constructs the client and inviter objects that later run the polling loop.

*Call graph*: calls 1 internal fn (_require_env); 4 external calls (__init__, __init__, getpid, gethostname).


##### `_require_env`  (lines 595–599)

```
def _require_env(name: str) -> str
```

**Purpose**: This reads one required environment variable and gives a clear error if it is missing. It is used only when Slack Connect delivery has been turned on.

**Data flow**: It receives an environment variable name, looks it up, and returns its value if it is set and non-empty. If not, it raises a startup error explaining which setting is required.

**Call relations**: slack_connect_from_env calls this for the Slack bot token and expected team ID. This keeps half-configured deployments from starting the Slack delivery workflow.

*Call graph*: called by 1 (slack_connect_from_env).

## 📊 State Registers Touched

- `reg-onboarding-claims` — Temporary signup, email-verification, invitation, and workspace-claim records used while a user joins.
- `reg-workspace-membership` — The roster of workspaces, members, admins, seats, and which people belong where.
- `reg-auth-tokens` — Signed passes that prove who a caller is or allow short-lived access to protected routes and links.
- `reg-agent-directory` — The saved assistants in each workspace and their settings, such as model behavior, sandbox size, and internet access.
- `reg-extension-pack-registry` — The selected packs and loaded extensions that decide which features, tools, routes, jobs, and backends exist.
- `reg-extension-store` — Per-workspace extension pins and extension-owned settings saved so enabled add-ons survive restarts.
- `reg-credential-vault` — Encrypted workspace secrets and short-lived brokered credentials used without exposing raw secrets to tools.
- `reg-surface-installations` — Mappings from outside entry points like Slack, web, terminal, and hosted surfaces into workspaces, members, and agents.
