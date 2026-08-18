# Hosted control plane startup and workspace onboarding  `stage-3`

This stage is the front door for hosted UFO onboarding. It runs when the control service starts and then supports the sign-up and sign-in flow for new users. The gateway builds the public web server, where a person installs the client, proves a work email, joins or creates a workspace, and receives a login token. The directive and web layers translate the same onboarding steps for two surfaces: the terminal client gets small text instructions, while the browser gets JSON for the sign-in page.

Behind the scenes, claim handling creates short-lived proof records for email ownership, stores them safely in Postgres, and checks them through WorkOS or a local development substitute. The invite logic keeps a one-use, time-limited invitation ledger for new company domains. The email helper rejects personal-looking addresses, builds invite messages, and sends them through Amazon SES or logs them locally. Shared workspace logic turns a verified address into membership or a new domain workspace. Token code issues the hosted login token. Slack Connect setup then creates a retry-safe customer channel and sends the first invite.

## Files in this stage

### Gateway surface
Public onboarding entrypoints expose the hosted gateway, terminal-client directives, and browser sign-in responses.

### `control/src/ufo_control/gateway.py`

`entrypoint` · `startup and request handling`

This file is the front door for onboarding. Without it, a new user could download the installer, but they would have no safe way to prove who they are, choose the right workspace, or receive credentials for the client. It serves both browser-based onboarding and terminal-based onboarding, using the same underlying flow so the rules stay consistent.

The central idea is an onboarding conversation. First, the server asks for a work email or receives one through Google sign-in via WorkOS, an identity provider service. Then it verifies the email, checks whether the email domain is allowed, and decides whether the user should join an existing workspace or create a new one. If creating a workspace requires an invite, the file enforces that gate before anything is opened. At the end, it mints a signed token, returns the workspace URL, and tells the client what prompt or menu to show next.

The file also starts the FastAPI web app, checks database health, serves the install script and client binaries, exposes login pages, and handles startup and shutdown. Think of it like an airport arrivals desk: it checks identity, checks whether the traveler has permission to enter, sends them to the right gate, and hands them the pass they need.

#### Function details

##### `_stamp_script`  (lines 107–109)

```
def _stamp_script(text: str) -> str
```

**Purpose**: This rewrites the bundled install script so it points at the public base URL for the current deployment. It lets the same script template work in production, staging, or local setups.

**Data flow**: It takes the script text as input, reads the public base URL from the environment if one is set, replaces the default URL line once, and returns the adjusted script text. It does not change files on disk.

**Call relations**: The module uses this when it builds the cached installer script served later by the /ufo route. That means requests do not have to restamp the script each time.


##### `Onboarding.advance`  (lines 126–137)

```
async def advance(self, channel: str, session: str, body: str, install: bytes) -> bytes
```

**Purpose**: This is the main state machine for a user's onboarding conversation. It looks at where the user is in the flow and moves them to the next step.

**Data flow**: It receives a channel, a session id, the user's latest text, and any installer-specific bytes. It asks the store whether this session already has a live claim. With no claim it collects an email, with an unverified claim it checks the code, and with a verified claim it resolves the workspace. It returns bytes containing instructions for the client or web page.

**Call relations**: The web and terminal onboarding routes call this after reading a request. It delegates to _collect_email, _verify_code, or _resolve depending on the stored claim state, so all surfaces follow one shared path.

*Call graph*: calls 3 internal fn (_collect_email, _resolve, _verify_code).


##### `Onboarding._collect_email`  (lines 139–161)

```
async def _collect_email(self, channel: str, session: str, body: str, install: bytes) -> bytes
```

**Purpose**: This runs the first step of onboarding: asking for a work email and starting verification. It prevents disallowed addresses from receiving a code.

**Data flow**: It receives the session details, the submitted body, and installer bytes. If the body is empty, it returns instructions asking for an email. If an email is present, it asks the claim workflow to validate it and send a code. On errors it returns the error and asks again; on success it tells the user a code was emailed.

**Call relations**: Onboarding.advance calls this when there is no existing claim for the session. It uses directive and render to turn the result into client-readable instructions.

*Call graph*: called by 1 (advance); 2 external calls (directive, render).


##### `Onboarding._verify_code`  (lines 163–172)

```
async def _verify_code(self, claim: OnboardClaim, body: str, install: bytes) -> bytes
```

**Purpose**: This checks the code the user typed after receiving an email verification message. It also copes with repeated or racing attempts by re-reading the current claim when verification fails.

**Data flow**: It receives the stored claim, the user's submitted code, and installer bytes. It asks the claim workflow to verify the code. If verification fails, it checks whether the claim became verified anyway and either continues to workspace resolution or returns an error prompt. If verification succeeds, it moves on to resolution.

**Call relations**: Onboarding.advance calls this for claims that exist but are not verified yet. It may hand off to _resolve once the email is trusted, or render another prompt if the user must try again.

*Call graph*: calls 1 internal fn (_resolve); called by 1 (advance); 2 external calls (directive, render).


##### `Onboarding._resolve`  (lines 174–214)

```
async def _resolve(self, claim: OnboardClaim, body: str, install: bytes) -> bytes
```

**Purpose**: This decides which workspace a verified user should enter. It can join an existing workspace, offer a list of choices, or create a new workspace when allowed.

**Data flow**: It receives a verified claim, the user's latest choice text, and installer bytes. It asks SharedWorkspaces for possible workspaces for the email domain and user. If none exist, it may require an invite before creating one. If choices exist, it may auto-join, ask the user to choose, or create a new workspace if an invite permits it. It records completion in the store and returns the signed-in response.

**Call relations**: This is reached from Onboarding.advance for already verified claims and from _verify_code after a code succeeds. It calls _invite_gate before creating when needed, _create to open a workspace, and _signed_in to finish the flow.

*Call graph*: calls 3 internal fn (_create, _invite_gate, _signed_in); called by 2 (_verify_code, advance); 2 external calls (directive, render).


##### `Onboarding._create`  (lines 216–220)

```
async def _create(self, claim: OnboardClaim) -> EnsuredWorkspace
```

**Purpose**: This creates a new workspace for the user's email domain. It includes any customer profile information collected with the invite.

**Data flow**: It receives the verified claim. It asks the invite system for the domain's profile, then asks SharedWorkspaces to create the workspace using the domain, email, and profile. It returns the ensured workspace record.

**Call relations**: _resolve calls this only after it has decided that a new workspace should be opened and any invite gate has passed.

*Call graph*: called by 1 (_resolve).


##### `Onboarding._invite_gate`  (lines 222–255)

```
async def _invite_gate(self, claim: OnboardClaim, install: bytes) -> bytes | None
```

**Purpose**: This enforces the rule that creating a new workspace may require an invite. It returns either a refusal message or permission to continue.

**Data flow**: It receives the claim and installer bytes. If the claim already has an invite id, it allows the flow to proceed. Otherwise it asks InviteCodes to redeem an invite for the email domain. Accepted invites return no refusal; expired, already used, or missing invites return rendered messages that end the session.

**Call relations**: _resolve calls this before creating a workspace when invites are required or when the user chooses to create one. It uses directive and render to make clear refusal screens.

*Call graph*: called by 1 (_resolve); 2 external calls (directive, render).


##### `Onboarding._signed_in`  (lines 257–283)

```
def _signed_in(self, claim: OnboardClaim, ensured: EnsuredWorkspace, install: bytes) -> bytes
```

**Purpose**: This builds the final successful onboarding response. It gives the user a token, the workspace URL, and the next prompt or menu.

**Data flow**: It receives the verified claim, the workspace result, and installer bytes. It mints a bearer token, decides whether the user is an operator or workspace admin, and renders directives such as token, workspace, debugger URL, sign-in text, Slack option, and next prompt. It returns those directives as bytes.

**Call relations**: _resolve calls this after the workspace has been joined or created and the store has been marked complete. It hands the finished sign-in package back to the route that started onboarding.

*Call graph*: called by 1 (_resolve); 3 external calls (directive, render, mint_token).


##### `GatewayState.healthy`  (lines 293–301)

```
async def healthy(self) -> bool
```

**Purpose**: This checks whether the gateway can still talk to the database using the expected roles. It is used to answer health checks safely.

**Data flow**: It reads the current database user from the owner connection pool and from a workspace transaction. If either query fails, it logs the failure and returns false. If both succeed, it compares the roles to the startup expectations and returns true only when they match.

**Call relations**: The /healthz route calls this when a health probe arrives. It uses workspace_tx for the serving-side database check and sqlalchemy text for the simple SQL statement.

*Call graph*: 2 external calls (text, workspace_tx).


##### `_require_env`  (lines 304–308)

```
def _require_env(name: str) -> str
```

**Purpose**: This reads a required environment variable and fails loudly if it is missing. It prevents the server from starting half-configured.

**Data flow**: It receives an environment variable name, reads its value, and returns the value if present. If the value is empty or unset, it raises a RuntimeError with a message naming the missing setting.

**Call relations**: gateway_app.lifespan calls this during startup for settings such as the workspace base URL and token secret. That keeps configuration errors at startup rather than first user request.

*Call graph*: called by 1 (lifespan).


##### `_invite_required`  (lines 311–320)

```
def _invite_required() -> bool
```

**Purpose**: This decides whether new workspace creation must be protected by invites. It defaults to requiring invites so a forgotten setting does not accidentally open signup.

**Data flow**: It reads UFO_INVITE_REQUIRED from the environment, treats true/1 as enabled and false/0 as disabled, and returns a boolean. If the value is anything else, it raises an error instead of guessing.

**Call relations**: gateway_app.lifespan calls this during startup and stores the result in the Onboarding object. Later, _resolve uses that policy through the Onboarding instance.

*Call graph*: called by 1 (lifespan).


##### `_dsn_role`  (lines 323–327)

```
def _dsn_role(dsn: str) -> str
```

**Purpose**: This extracts the database username from a database connection string. The gateway uses it to remember which role each database connection should be using.

**Data flow**: It receives a DSN, meaning a database connection URL. It parses the URL, returns the username part, and raises an error if there is no username.

**Call relations**: gateway_app.lifespan calls this for the owner and serving database URLs. GatewayState.healthy later compares live database roles against these stored values.

*Call graph*: called by 1 (lifespan).


##### `_request_body`  (lines 330–336)

```
async def _request_body(request: Request) -> str
```

**Purpose**: This safely reads a small text request body. It protects the onboarding endpoints from oversized submissions.

**Data flow**: It receives a FastAPI request, reads the body stream chunk by chunk, and stops with _RequestInputError if the body exceeds the configured limit. Otherwise it decodes the collected bytes as UTF-8, trims surrounding whitespace, and returns the text.

**Call relations**: Both onboard_web and onboard call this before passing user input into Onboarding.advance. It uses the request stream directly so size limits are enforced while reading.

*Call graph*: called by 2 (onboard, onboard_web); 2 external calls (__init__, stream).


##### `_onboard_session`  (lines 339–351)

```
def _onboard_session(request: Request, secret: str) -> str | None
```

**Purpose**: This finds a valid sealed onboarding session cookie in a browser request. It only trusts a cookie value that the server can verify.

**Data flow**: It receives the request and the signing secret. It scans the Cookie header for the onboarding cookie name, tries to open each matching value with the secret, and returns the first value that verifies. If none verify, it returns null.

**Call relations**: auth_callback uses this to confirm that a Google sign-in return belongs to the browser that started it. onboard_web uses it to continue an existing browser onboarding session or decide to mint a new one.

*Call graph*: called by 2 (auth_callback, onboard_web); 1 external calls (open_session).


##### `gateway_app`  (lines 354–592)

```
def gateway_app() -> FastAPI
```

**Purpose**: This constructs the FastAPI application and registers all HTTP routes for the gateway. It is the factory that turns the onboarding logic into a running web service.

**Data flow**: It creates a local state holder, defines startup and shutdown behavior, attaches routes for health, installer files, login, WorkOS authentication, and onboarding, and returns the configured FastAPI app. The module then calls it to expose the app object.

**Call relations**: This top-level function calls FastAPI to create the server object and checks whether the WorkOS console-mode route should be mounted. The nested route functions close over the shared GatewayState initialized by lifespan.

*Call graph*: 2 external calls (FastAPI, workos_console_mode).


##### `gateway_app.lifespan`  (lines 358–412)

```
async def lifespan(app: FastAPI)
```

**Purpose**: This is the startup and shutdown routine for the web server. It prepares database access, onboarding services, invite checks, and optional Slack background work.

**Data flow**: On startup it reads required environment settings, verifies the control schema, opens an async database pool, builds the store, invite system, workspace resolver, verifier, and Onboarding object, initializes the serving database layer, and starts any Slack poller task. On shutdown it cancels background tasks, clears state, disposes database resources, and closes the pool.

**Call relations**: FastAPI runs this around the app's lifetime. It calls helper functions such as _require_env, _invite_required, and _dsn_role, then creates the objects that every request route later uses.

*Call graph*: calls 3 internal fn (_dsn_role, _invite_required, _require_env); 18 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, create_task, gather, create_pool (+8 more)).


##### `gateway_app.healthz`  (lines 417–420)

```
async def healthz() -> Response
```

**Purpose**: This answers health check requests. It tells load balancers or monitoring tools whether the gateway is ready to serve traffic.

**Data flow**: It reads the shared state. If state is missing or the database role checks fail, it returns a JSON unavailable response with status 503. Otherwise it returns JSON saying ok.

**Call relations**: The /healthz route calls GatewayState.healthy to do the real check. It wraps that result in a JSONResponse for HTTP clients.

*Call graph*: 1 external calls (JSONResponse).


##### `gateway_app.serve_script`  (lines 423–424)

```
async def serve_script() -> Response
```

**Purpose**: This serves the shell installer script for the UFO client. It is what a user or install command downloads from /ufo.

**Data flow**: It takes no user body. It returns the stamped installer script text using the shell script media type.

**Call relations**: The /ufo route uses the module-level STAMPED_SCRIPT prepared by _stamp_script. It returns a PlainTextResponse because the installer is plain text.

*Call graph*: 1 external calls (PlainTextResponse).


##### `gateway_app.client_binary`  (lines 427–438)

```
async def client_binary(target: str) -> Response
```

**Purpose**: This serves a native UFO client binary for a supported operating system and CPU target. It returns a normal not-found response when the deployment has no matching file.

**Data flow**: It receives a target name from the URL, reads the configured binary directory, checks that the target is allowed, chooses the executable filename, and verifies the file exists. If all checks pass it streams the file; otherwise it returns a 404 text response.

**Call relations**: The /ufo/bin/{target} route calls this when an installer asks for a binary. It uses Path to find the file and FileResponse to send it.

*Call graph*: 3 external calls (Path, FileResponse, PlainTextResponse).


##### `gateway_app.fleet`  (lines 441–444)

```
async def fleet() -> Response
```

**Purpose**: This returns a small count of workspaces. It appears to be a lightweight fleet-size endpoint.

**Data flow**: It uses the shared database pool to count rows in the workspace table and returns JSON with that count under the name craft.

**Call relations**: The /fleet route calls directly into the startup-created pool. It depends on lifespan having initialized state.

*Call graph*: 1 external calls (JSONResponse).


##### `gateway_app.login`  (lines 447–448)

```
async def login() -> Response
```

**Purpose**: This serves the browser login page. The page drives web onboarding and Google sign-in.

**Data flow**: It takes no body, wraps the LOGIN_PAGE HTML string in an HTML response, and returns it to the browser.

**Call relations**: The /login route uses this whenever a person opens the web sign-in page or is redirected back there after WorkOS authentication.

*Call graph*: 1 external calls (HTMLResponse).


##### `gateway_app.auth_start`  (lines 451–478)

```
async def auth_start(request: Request) -> Response
```

**Purpose**: This starts browser sign-in through Google via WorkOS. It creates a protected onboarding session and sends the browser to the external authorization URL.

**Data flow**: It reads optional conversation and artifact query parameters, creates a random session id, seals it with the token secret, packs that data into signed state, and builds a redirect to the verifier's authorization URL. It also sets the sealed session as a secure cookie before returning the redirect.

**Call relations**: The login page's Continue with Google flow targets this route. It uses pack_state, seal_session, and set_session_cookie so auth_callback can later prove the returning browser is the same one.

*Call graph*: 7 external calls (__init__, token_urlsafe, RedirectResponse, set_session_cookie, pack_state, seal_session, unpack_state).


##### `gateway_app.auth_console`  (lines 483–487)

```
async def auth_console(request: Request) -> Response
```

**Purpose**: This serves a local development stand-in for the external Google sign-in step. It exists only when WorkOS console mode is enabled.

**Data flow**: It reads the state query parameter and returns an HTML page that lets a developer enter a work email for testing. It does not perform production authentication itself.

**Call relations**: gateway_app only registers this route when workos_console_mode is true. The page it returns is part of the same start-to-callback story used by auth_start and auth_callback.

*Call graph*: 2 external calls (HTMLResponse, console_signin_page).


##### `gateway_app.auth_callback`  (lines 490–525)

```
async def auth_callback(request: Request) -> Response
```

**Purpose**: This receives the browser after Google or the console-mode sign-in returns. It verifies that the signed state and secure cookie match before admitting the email as verified.

**Data flow**: It reads the code and signed state from the query string, unpacks the state, rebuilds any return query values, and checks the sealed onboarding cookie. If the code is missing, the cookie is missing, or the cookie does not match the state session, it records a sign-in failure. Otherwise it exchanges the code for an email and admits that email as a verified web claim. It redirects back to /login, optionally carrying an error message.

**Call relations**: This completes the browser path started by auth_start. It calls _onboard_session to check the browser-bound session and uses the onboarding verifier and claim workflow before redirecting to the login page.

*Call graph*: calls 1 internal fn (_onboard_session); 6 external calls (__init__, compare_digest, PlainTextResponse, RedirectResponse, unpack_state, urlencode).


##### `gateway_app.onboard_web`  (lines 528–560)

```
async def onboard_web(request: Request) -> Response
```

**Purpose**: This is the browser onboarding API endpoint. It advances the same onboarding machine used by the terminal, but stores the session in a secure cookie and returns JSON directives.

**Data flow**: It reads or mints a sealed browser session, making a fresh one if there is no trusted live claim. It reads the request body with a size limit, calls Onboarding.advance for the web channel, parses the returned directives into JSON, and sets a new session cookie when one was minted. On input or unexpected errors, it returns directives that explain the failure.

**Call relations**: The login page calls this endpoint during web onboarding. It uses _onboard_session and _request_body before handing work to Onboarding.advance, then uses parse_directives to turn the shared directive format into browser-friendly JSON.

*Call graph*: calls 2 internal fn (_onboard_session, _request_body); 7 external calls (token_urlsafe, JSONResponse, set_session_cookie, directive, render, parse_directives, seal_session).


##### `gateway_app.onboard`  (lines 563–590)

```
async def onboard(channel: str, request: Request) -> Response
```

**Purpose**: This is the terminal or non-web onboarding API endpoint. It advances onboarding for a named channel using a session supplied in a request header.

**Data flow**: It receives the channel from the URL, reads the x-ufo-session header, and prepares installer information from headers. If the session is missing or the channel/session are too long, it returns rendered error instructions. Otherwise it reads the body, calls Onboarding.advance, and returns the resulting plain-text directive stream.

**Call relations**: Terminal clients post here as the user answers prompts. It calls _request_body for safe input reading and then delegates the real flow to Onboarding.advance, returning render/directive output that the client understands.

*Call graph*: calls 1 internal fn (_request_body); 5 external calls (__init__, PlainTextResponse, client_install, directive, render).


### `control/src/ufo_control/gateway_directives.py`

`io_transport` · `request handling`

The UFO terminal client receives simple server-written commands over a wire format: one command per line, with fields separated by tabs. This file is the small translator that turns server intentions into those bytes. Without it, different parts of the server might format client instructions differently, and the terminal client could misread them.

The main idea is simple: a directive is like a row in a spreadsheet. The first cell is the command name, such as "install", and the remaining cells are optional details. Because real text can contain tabs, newlines, or backslashes, `directive` safely escapes those characters before sending them. `render` then joins several already-built byte lines into one response body.

The file also reads request headers in a forgiving way. HTTP header names are meant to be case-insensitive, so `header_value` finds a header even if the caller used different capitalization.

The most important decision here is in `client_install`. It checks whether the incoming client says it is already installed, and whether its script version matches the version this server is serving. If not, it returns an `install` directive. This lets a fresh `curl | sh` setup or an outdated client update itself automatically, while avoiding repeated installs once the client reports the expected state.

#### Function details

##### `directive`  (lines 10–15)

```
def directive(verb: str, *fields: str) -> bytes
```

**Purpose**: Builds one client directive as bytes in the exact line format the terminal client expects. It protects special characters in the fields so the client can split and read the command safely.

**Data flow**: It takes a command word and any number of text fields. It rewrites backslashes, tabs, and newlines into safe escape sequences, removes carriage returns, joins everything with tab characters, adds a final newline, and returns the result as bytes ready to send.

**Call relations**: When `client_install` decides the client needs installation or an update, it asks `directive` to create the actual `install` message. Other code can also use it as the standard way to format any future server-to-client instruction.

*Call graph*: called by 1 (client_install).


##### `render`  (lines 18–19)

```
def render(*lines: bytes) -> bytes
```

**Purpose**: Combines several already-created directive byte strings into one byte string. This is useful when the server wants to send multiple client instructions together.

**Data flow**: It takes any number of byte chunks as input. It places them one after another without adding anything extra, and returns the combined bytes.

**Call relations**: This function is a simple packaging step for directive output. It does not call other helpers in this file; it exists so higher-level code can assemble a full response from individual directive lines.


##### `header_value`  (lines 22–27)

```
def header_value(headers: Mapping[str, str], name: str) -> str | None
```

**Purpose**: Looks up an HTTP-style header without caring about capitalization. This matters because headers such as `X-UFO-Installed` and `x-ufo-installed` should mean the same thing.

**Data flow**: It receives a mapping of header names to values and the header name to find. It compares names in lowercase form, returns the matching value if found, and returns `null` if no matching header is present.

**Call relations**: `client_install` uses this helper to read the client’s installation and script-version headers reliably. By keeping the case-insensitive lookup here, the install decision stays focused on what the headers mean rather than how they are spelled.

*Call graph*: called by 1 (client_install).


##### `client_install`  (lines 30–40)

```
def client_install(headers: Mapping[str, str]) -> bytes
```

**Purpose**: Decides whether the server should tell the terminal client to install or update itself. It returns an `install` directive only when the client is missing, not marked as installed, or running a different served script version.

**Data flow**: It receives the request headers. First it checks whether `x-ufo-installed` equals `1`; if not, it returns an `install` directive. If the client is installed, it reads the server’s expected client version from the `UFO_CLIENT_VERSION` environment variable. When that version is set and the client’s `x-ufo-script` header does not match it, it again returns an `install` directive. If everything looks current, it returns empty bytes, meaning there is no install instruction to send.

**Call relations**: This is the file’s main decision point. It calls `header_value` to read headers in a case-insensitive way, and calls `directive` when it needs to produce the actual `install` command. Higher-level gateway code can call this during a request to prepend an install/update instruction to the client’s screen output.

*Call graph*: calls 2 internal fn (directive, header_value).


### `control/src/ufo_control/gateway_web.py`

`io_transport` · `request handling`

This file is the web skin for the same onboarding flow that the terminal client uses. The important idea is that the browser does not run a separate sign-in process. Instead, it talks to the same onboarding state machine and displays its instructions, much like a different screen attached to the same engine.

The page stored in LOGIN_PAGE is a complete HTML, CSS, and JavaScript sign-in page. It can ask for an email, accept a code, offer “Continue with Google,” show error messages, and finally display the signed-in home card with the workspace link and terminal install command. The JavaScript sends answers to /v1/onboard/web, receives a list of “directives,” and turns them into visible page changes. A directive is a small instruction such as “say this line,” “ask this question,” “store this token,” or “show the workspace.”

The Python helper parse_directives is the bridge between the backend’s compact text format and the browser’s JSON format. The backend separates directive fields with tabs and lines, so this file carefully reverses escaping for real tabs, newlines, and backslashes inside field values. Without this file, the web login page would either not exist or would not be able to correctly read the onboarding machine’s instructions.

#### Function details

##### `parse_directives`  (lines 40–49)

```
def parse_directives(payload: bytes) -> list[dict[str, object]]
```

**Purpose**: This function turns the onboarding machine’s byte output into a list of simple dictionaries that can be sent as JSON to the browser. It exists so the web page can receive clear instructions instead of raw tab-separated text.

**Data flow**: It receives bytes containing one directive per line. It decodes the bytes into text, skips empty lines, splits each line into a verb and its tab-separated fields, then unescapes each field so stored tabs, newlines, and backslashes become real characters again. The result is a list like “verb plus fields” dictionaries, ready for JSON output.

**Call relations**: When the web endpoint has raw directive text from the onboarding flow, this function prepares it for the browser. As part of that work, it calls control/src/ufo_control/gateway_web._unescape on every field, so the browser sees the original human text rather than the escaped wire format.

*Call graph*: calls 1 internal fn (_unescape).


##### `_unescape`  (lines 52–65)

```
def _unescape(field: str) -> str
```

**Purpose**: This function restores special characters inside one directive field. It is needed because directive lines use escape sequences to safely fit tabs, newlines, and backslashes into a line-based format.

**Data flow**: It receives one text field that may contain escape sequences such as \t, \n, or \\. It walks through the text character by character, replacing known escape sequences with the real tab, newline, or backslash character, while leaving ordinary text unchanged. It returns the cleaned-up field string.

**Call relations**: This is a small helper used by control/src/ufo_control/gateway_web.parse_directives. parse_directives splits the larger directive payload into fields, then hands each field here so the original text can be reconstructed before it is sent to the web page.

*Call graph*: called by 1 (parse_directives).


### Email claim verification
Work-email ownership is proven through short-lived onboarding claims backed by Postgres and WorkOS or local-development verification.

### `control/src/ufo_control/gateway_claim.py`

`domain_logic` · `request handling during onboarding email verification`

This file protects onboarding from two common problems: people using emails they should not use, and verification attempts getting stale or crossed with each other. A claim is like a numbered ticket at a service desk: it belongs to one email, one onboarding surface, and one short time window. The time window is ten minutes, matching WorkOS Magic Auth codes. That matters because WorkOS does not clearly say whether a bad code is wrong or expired, so this workflow uses the claim’s expiry time to give the user a clearer message.

The main class, ClaimWorkflow, sits between three parts: an email policy that decides whether an address is allowed, a store that saves claims, and a verifier that talks to WorkOS. For terminal-style onboarding, start creates an unverified claim and asks WorkOS to send a code. Later, verify checks whether the claim is still alive, asks WorkOS if the code is correct, and marks the claim as verified only if nothing else changed first.

For browser-style onboarding, WorkOS has already verified the user before returning here. admit_verified writes a claim that is verified from the start, or reuses an existing live claim for the same browser session. Several writes are deliberately conditional, so if two attempts race each other, the losing attempt gets a safe “session changed” message instead of corrupting state.

#### Function details

##### `Verifier.authorization_url`  (lines 30–30)

```
def authorization_url(self, state: str) -> str
```

**Purpose**: This defines the shape of a verifier method that can build a browser sign-in or verification link. A real verifier, such as a WorkOS-backed one, supplies the actual implementation.

**Data flow**: It receives a state value, which is usually a random or session-tied marker used to connect the browser return trip to the original request. It returns a URL string that the user can visit to continue verification.

**Call relations**: ClaimWorkflow depends on a Verifier rather than hard-coding WorkOS directly. This method is part of that contract, so browser onboarding code elsewhere can ask the verifier for a safe redirect URL while this file stays focused on claim state.


##### `Verifier.exchange`  (lines 31–31)

```
async def exchange(self, code: str) -> str
```

**Purpose**: This defines the shape of a verifier method that trades a browser callback code for a verified email address. The real implementation talks to the outside identity service.

**Data flow**: It receives a short code from the browser callback. It checks or redeems that code through the verifier service and returns the email address that the service says is verified.

**Call relations**: This method belongs to the same verifier contract used by the onboarding flow. After another part of the system exchanges a browser code and gets an email, ClaimWorkflow.admit_verified can create or reuse a verified claim for that email.


##### `Verifier.begin`  (lines 32–32)

```
async def begin(self, email: str) -> None
```

**Purpose**: This defines the shape of a verifier method that starts an email-code verification. In practice, it asks the verification provider to send a code to the user’s work email.

**Data flow**: It receives an email address. It triggers the outside verifier to begin verification for that email and returns nothing if the request was accepted; it may raise an error if the provider refuses or fails.

**Call relations**: ClaimWorkflow.start calls this after saving a new unverified claim. If begin fails, start removes the claim again so the database does not keep a claim for an email code that was never sent.


##### `Verifier.confirm`  (lines 33–33)

```
async def confirm(self, email: str, code: str) -> bool
```

**Purpose**: This defines the shape of a verifier method that checks whether a user-entered email code is valid. It hides the details of the outside verification service behind a simple yes-or-no answer.

**Data flow**: It receives the email address and the code the user typed. It asks the verifier service whether they match, then returns true for a confirmed code or false for an incorrect code; it may raise an error if verification itself fails.

**Call relations**: ClaimWorkflow.verify calls this only after it has checked that the local claim has not expired. The result decides whether the claim can be marked verified or whether the user should be told the code is incorrect.


##### `ClaimWorkflow.start`  (lines 47–57)

```
async def start(self, email: str, surface: str, surface_ref: str) -> str
```

**Purpose**: This begins a new work-email verification attempt. It checks that the email is allowed, creates a temporary unverified claim, saves it, and asks the verifier to send a code.

**Data flow**: It takes an email address plus the onboarding surface and its reference, such as where this onboarding attempt came from. It validates the email domain, builds a claim, stores it, and then asks the verifier to begin email verification. If sending the code fails, it deletes the claim and raises a user-facing ClaimError; if everything works, it returns the accepted email domain.

**Call relations**: This is the first step for code-based onboarding. It uses ClaimWorkflow._claim to make the stored claim, then hands the email to Verifier.begin. Later, the saved claim is passed into ClaimWorkflow.verify when the user enters the code.

*Call graph*: calls 1 internal fn (_claim); 1 external calls (__init__).


##### `ClaimWorkflow.verify`  (lines 59–73)

```
async def verify(self, claim: OnboardClaim, code: str) -> None
```

**Purpose**: This checks a user’s verification code and, if it is valid and still timely, marks the onboarding claim as verified. It also gives different messages for expired codes, wrong codes, provider errors, and verification attempts that have been changed by another process.

**Data flow**: It receives an existing claim and the code the user typed. First it compares the current time with the claim’s expiry time. If the claim is too old, it tries to delete the still-unverified claim and reports expiry. If the claim is still live, it asks the verifier to confirm the code. A false answer becomes an “incorrect code” message; a true answer leads to a conditional store update that stamps the claim as verified. If another attempt has already changed or removed the claim, it reports that the session changed.

**Call relations**: This is the follow-up to ClaimWorkflow.start. It calls the verifier through Verifier.confirm and relies on the store’s conditional delete and mark-verified operations to avoid races, such as two code submissions happening almost at the same time.

*Call graph*: 2 external calls (__init__, now).


##### `ClaimWorkflow.admit_verified`  (lines 75–85)

```
async def admit_verified(self, email: str, surface: str, surface_ref: str) -> OnboardClaim
```

**Purpose**: This creates a verified claim for browser-based onboarding, where WorkOS has already proved the email before this function is called. It also avoids opening duplicate claims for the same live browser session.

**Data flow**: It receives a verified email plus the onboarding surface and surface reference. It validates the email domain, asks the store whether a live claim already exists for that surface, and returns that existing claim if found. Otherwise it builds a new claim with the current time as its verified timestamp, stores it, and returns it.

**Call relations**: This is used after the browser verification path has already succeeded elsewhere, likely after Verifier.exchange has returned an email. It uses ClaimWorkflow._claim to create the stored record, but unlike ClaimWorkflow.start, it does not call Verifier.begin because no new email code needs to be sent.

*Call graph*: calls 1 internal fn (_claim); 1 external calls (now).


##### `ClaimWorkflow._claim`  (lines 87–99)

```
def _claim(self, email: str, domain: str, surface: str, surface_ref: str, verified_at: datetime | None) -> OnboardClaim
```

**Purpose**: This is the small factory that creates a complete OnboardClaim record in one consistent way. It fills in the unique ID, normalized email, expiry time, verification timestamp, and related onboarding details.

**Data flow**: It receives the email, approved domain, surface, surface reference, and either a verification time or no verification time. It trims and lowercases the email, creates a fresh unique claim ID, sets the expiry time to now plus the claim time-to-live, and returns a new OnboardClaim object ready to save.

**Call relations**: ClaimWorkflow.start calls it to create an unverified claim before sending an email code. ClaimWorkflow.admit_verified calls it to create a claim that is already verified because the browser flow has finished verification.

*Call graph*: called by 2 (admit_verified, start); 3 external calls (__init__, now, uuid4).


### `control/src/ufo_control/gateway_store.py`

`io_transport` · `onboarding request handling`

Onboarding often needs a short-lived paper trail: someone starts from a particular place, such as an invite link or hosted surface, proves they own an email address, and then either joins an existing workspace or creates a new one. This file stores that paper trail in Postgres, a database used for durable shared state.

The table definition at the top describes the ledger. Each claim has an id, email details, where the claim came from, when it expires, whether it has been verified, and what workspace it finally led to. A special unique index makes sure there is only one unfinished claim for the same surface and reference. In everyday terms, it prevents two open tickets for the same doorway.

The `OnboardClaim` data object is the in-memory shape of a row from that table. `OnboardStore` is the small database wrapper that uses an asyncpg connection pool, meaning it borrows database connections as needed without blocking the whole program. Its methods insert a claim, fetch the current unfinished claim, mark email verification, record completion, or delete claims. A small helper, `_aware`, makes sure timestamps coming back from the database include timezone information, which avoids subtle time comparison mistakes later.

#### Function details

##### `_aware`  (lines 43–46)

```
def _aware(value: datetime | None) -> datetime | None
```

**Purpose**: This helper makes sure a timestamp has timezone information. If the database gives back a plain datetime without a timezone, it treats it as UTC, the common world clock used to compare times safely.

**Data flow**: It receives either a datetime value or nothing. If it receives nothing, it returns nothing. If it receives a datetime that already has a timezone, it returns it unchanged. If the datetime has no timezone, it adds UTC and returns the corrected value.

**Call relations**: When `OnboardStore.live_claim` rebuilds an `OnboardClaim` from a database row, it calls `_aware` for the stored expiration and verification times. `_aware` may use the datetime object's `replace` operation to attach UTC when needed.

*Call graph*: called by 1 (live_claim); 1 external calls (replace).


##### `OnboardStore.insert_claim`  (lines 53–66)

```
async def insert_claim(self, claim: OnboardClaim) -> None
```

**Purpose**: This adds a new onboarding claim to the database. The rest of the onboarding flow uses it when someone starts the process and the system needs a durable record to come back to later.

**Data flow**: It receives an `OnboardClaim` object with the claim id, email, source surface, source reference, expiration time, and optional verification time. It borrows a database connection from the pool and inserts those values into the onboarding claim table. It does not return a value; the lasting result is the new database row.

**Call relations**: This is an entry point into the store from the broader onboarding flow. It does not call other project functions; it hands the claim directly to Postgres through the asyncpg connection.


##### `OnboardStore.live_claim`  (lines 68–87)

```
async def live_claim(self, surface: str, surface_ref: str) -> OnboardClaim | None
```

**Purpose**: This finds the still-open onboarding claim for a particular surface and reference. It is used when the system needs to resume or inspect a claim that has not yet produced a workspace.

**Data flow**: It receives a surface name and a surface reference, such as the place where onboarding began and that place's identifier. It queries the database for a row with those values where no resulting workspace has been recorded yet. If no row exists, it returns `None`. If a row exists, it converts the row into an `OnboardClaim`, cleaning up timestamp timezone information along the way.

**Call relations**: This method is called by the onboarding flow when it needs the current unfinished claim for a doorway. After reading from Postgres, it calls `_aware` to normalize timestamps and then constructs an `OnboardClaim` object for the rest of the code to use.

*Call graph*: calls 1 internal fn (_aware); 1 external calls (__init__).


##### `OnboardStore.mark_verified`  (lines 89–96)

```
async def mark_verified(self, claim_id: UUID) -> bool
```

**Purpose**: This records that a claim's email proof has been accepted. It is careful to only mark claims that were not already verified, so callers can tell whether this was the first successful verification.

**Data flow**: It receives a claim id. It updates that database row by setting `verified_at` to the database server's current time, but only if `verified_at` is still empty. It returns `true` if it actually recorded the verification, and `false` if the claim was missing or had already been verified.

**Call relations**: The verification step of onboarding calls this after an email link, code, or similar proof succeeds. The method sends the update straight to Postgres and returns a simple yes-or-no result to the caller.


##### `OnboardStore.complete`  (lines 98–111)

```
async def complete(self, claim_id: UUID, resulting_workspace_id: str, *, created_workspace: bool) -> None
```

**Purpose**: This closes an onboarding claim by recording which workspace it ended in. It also records whether that workspace was newly created or whether the person joined one that already existed.

**Data flow**: It receives a claim id, the resulting workspace id, and a `created_workspace` flag. It updates the matching database row with that final workspace id and the true-or-false creation marker. It returns nothing; the database row becomes the permanent completion record.

**Call relations**: The later part of onboarding calls this after the system has decided where the person belongs. Once this runs, `live_claim` will no longer treat the claim as open, because the row now has a resulting workspace id.


##### `OnboardStore.delete_claim`  (lines 113–115)

```
async def delete_claim(self, claim_id: UUID) -> None
```

**Purpose**: This removes an onboarding claim from the database without checking its verification state. It is useful for cleanup or cancellation when the system has decided the claim should disappear.

**Data flow**: It receives a claim id. It borrows a database connection and deletes the row with that id. It returns nothing; the only effect is that the row is gone if it existed.

**Call relations**: Cleanup code in the onboarding flow can call this when a claim should be discarded outright. The method does not hand off to other project code; it performs a direct database delete.


##### `OnboardStore.delete_unverified_claim`  (lines 117–123)

```
async def delete_unverified_claim(self, claim_id: UUID) -> bool
```

**Purpose**: This removes a claim only if the email has not been verified yet. It protects verified claims from being accidentally erased by cleanup meant for abandoned or failed starts.

**Data flow**: It receives a claim id. It asks the database to delete that row only when `verified_at` is still empty. It returns `true` if a row was deleted and `false` if the claim was already verified, did not exist, or otherwise did not match the condition.

**Call relations**: The onboarding flow can use this for safe cancellation before verification. Like `mark_verified`, it uses the database's conditional update/delete behavior to give the caller a clear yes-or-no answer about what happened.


### `control/src/ufo_control/gateway_workos.py`

`domain_logic` · `startup and sign-in request handling`

This file supports onboarding sign-in without letting callers invent trusted identity data. In normal use, it talks to WorkOS, an outside identity service, to send email codes or send a browser through Google sign-in. In local development, it can switch to a console mode that fakes only the proof step, so developers can test the same gateway flow without real WorkOS credentials.

A key part of the file is protecting small pieces of sign-in state. When the browser leaves for Google and comes back, the gateway must remember which onboarding session it belongs to. The file packs that into a signed “state” value. A signature is like a tamper-evident seal: anyone can carry the package, but only this gateway can make one that opens as trusted. It also seals session cookies the same way, using a separate signing key label so a cookie seal and a browser state seal cannot be confused.

The two verifier classes provide the same simple shape. `WorkosVerifier` calls WorkOS to start email-code verification, confirm a code, or exchange a Google return code for an email address. `ConsoleVerifier` behaves the same from the gateway’s point of view, but logs a fixed code and accepts a local form. The environment decides which verifier is used.

#### Function details

##### `pack_state`  (lines 62–68)

```
def pack_state(carry: AuthCarry, secret: str) -> str
```

**Purpose**: Builds the browser “state” value used during OAuth sign-in. This lets the gateway remember the onboarding session, and optionally where the user was headed, without trusting data that comes back from the browser unless it has this gateway’s signature.

**Data flow**: It receives an `AuthCarry` object containing a session id, possible conversation id, and possible artifact path, plus a secret. It turns those values into JSON, encodes that JSON into a URL-safe text form, signs that text with `_state_signature`, and returns one combined string containing both the data and the signature.

**Call relations**: When the sign-in flow needs to send the browser away and later recognize it again, this function prepares the carry-along package. It relies on `_state_signature` to add the tamper-evident seal before the package is handed to the browser.

*Call graph*: calls 1 internal fn (_state_signature); 2 external calls (urlsafe_b64encode, dumps).


##### `unpack_state`  (lines 71–97)

```
def unpack_state(raw: str, secret: str) -> AuthCarry
```

**Purpose**: Reads a returned OAuth state value and decides whether it was really made by this gateway. If it is valid, it extracts the onboarding session and keeps only safe-looking optional destination data.

**Data flow**: It receives the raw state string and the same secret used to pack it. It separates the encoded body from its signature, checks the signature, decodes the JSON, verifies there is a usable session, drops malformed conversation or artifact values, and returns an `AuthCarry`. If the state is missing, forged, oversized, or unreadable, it raises `ValueError` instead of trusting it.

**Call relations**: This is the return-trip partner to `pack_state`. It calls `_state_signature` to recompute the expected seal, uses a constant-time comparison so attackers cannot learn the signature bit by bit, then creates the `AuthCarry` that the callback flow can use.

*Call graph*: calls 1 internal fn (_state_signature); 4 external calls (__init__, urlsafe_b64decode, compare_digest, loads).


##### `_state_signature`  (lines 100–104)

```
def _state_signature(body: str, secret: str) -> str
```

**Purpose**: Creates the cryptographic signature for an OAuth state body. The signature proves that the state was produced by this gateway and was not edited in the browser.

**Data flow**: It receives the encoded state body and the gateway secret. It first derives a state-specific signing key from the secret, then signs the body with that derived key, and returns the signature as hexadecimal text.

**Call relations**: `pack_state` calls it when sealing a state value, and `unpack_state` calls it again when checking a returned value. It is kept separate so state signing consistently uses its own key label.

*Call graph*: called by 2 (pack_state, unpack_state); 1 external calls (new).


##### `seal_session`  (lines 107–112)

```
def seal_session(session: str, secret: str) -> str
```

**Purpose**: Turns a session id into the value stored in the onboarding cookie. The result can later prove it came from this gateway, not from a user or attacker making up a cookie.

**Data flow**: It receives a session string and a secret. It signs the session with `_cookie_signature`, joins the session and signature with a separator, and returns the sealed cookie value.

**Call relations**: This is used when the gateway mints or refreshes an onboarding session cookie. It hands the actual signing work to `_cookie_signature`, which uses a cookie-specific signing key.

*Call graph*: calls 1 internal fn (_cookie_signature).


##### `open_session`  (lines 115–124)

```
def open_session(value: str, secret: str) -> str | None
```

**Purpose**: Checks a sealed onboarding cookie and extracts the session id only if the seal is valid. A forged or broken cookie is treated as absent rather than trusted.

**Data flow**: It receives a cookie value and the gateway secret. It splits out the claimed session and signature, recomputes the expected cookie signature, compares them safely, and returns the session string if they match. If the session is empty or the signature is wrong, it returns `None`.

**Call relations**: This is the checking partner to `seal_session`. It calls `_cookie_signature` to recreate the expected seal and is used by the wider gateway flow before using a cookie to find an onboarding claim.

*Call graph*: calls 1 internal fn (_cookie_signature); 1 external calls (compare_digest).


##### `_cookie_signature`  (lines 127–131)

```
def _cookie_signature(session: str, secret: str) -> str
```

**Purpose**: Creates the cryptographic signature for an onboarding session cookie. It keeps cookie signing separate from OAuth state signing, even though both use the same gateway secret as their root.

**Data flow**: It receives a session id and secret. It derives a cookie-specific key from that secret, signs the session id, and returns the signature as hexadecimal text.

**Call relations**: `seal_session` uses it to make cookie values, and `open_session` uses it to check cookie values. Its separate key label prevents a valid signature in one context from being reused as if it belonged to the other.

*Call graph*: called by 2 (open_session, seal_session); 1 external calls (new).


##### `WorkosVerifier.authorization_url`  (lines 141–146)

```
def authorization_url(self, state: str) -> str
```

**Purpose**: Builds the URL that sends a browser to Google sign-in through WorkOS. This is used for the “Continue with Google” path.

**Data flow**: It receives the signed state string. It asks the WorkOS client to create an authorization URL using Google as the provider, the configured redirect URI, and that state, then returns the URL for the browser to visit.

**Call relations**: The onboarding flow calls this when it needs to start browser-based sign-in. It delegates URL creation to the WorkOS SDK so the gateway uses WorkOS’s expected parameters.


##### `WorkosVerifier.exchange`  (lines 148–153)

```
async def exchange(self, code: str) -> str
```

**Purpose**: Turns the short return code from Google/WorkOS into the verified email address. This is how the gateway learns which email address the user proved control over.

**Data flow**: It receives a code from the sign-in callback. It sends that code to WorkOS, receives a user record, trims and lowercases the email address, and returns it. If WorkOS reports a failure, it raises `VerificationError` with a user-friendly message.

**Call relations**: The callback flow uses this after the browser returns from the Google sign-in hop. It depends on WorkOS for the identity proof and wraps WorkOS errors so the rest of onboarding can show a simple sign-in failure.

*Call graph*: 1 external calls (__init__).


##### `WorkosVerifier.begin`  (lines 155–159)

```
async def begin(self, email: str) -> None
```

**Purpose**: Starts an email-code verification by asking WorkOS to send a magic-auth code to the given address. This supports the flow where the user types a code instead of using Google.

**Data flow**: It receives an email address. It asks WorkOS to create a magic-auth challenge for that address, which causes WorkOS to send the code. It returns nothing on success, and raises `VerificationError` if the code could not be sent.

**Call relations**: The onboarding flow calls this after it has accepted the email address as eligible. It passes the sending job to WorkOS and turns any WorkOS failure into a message the gateway can render.

*Call graph*: 1 external calls (__init__).


##### `WorkosVerifier.confirm`  (lines 161–170)

```
async def confirm(self, email: str, code: str) -> bool
```

**Purpose**: Checks whether a user-entered email code is correct for the given email address. It treats a wrong, expired, or already-used code as something the user may retry, while other failures stop the attempt.

**Data flow**: It receives an email address and code. It asks WorkOS to authenticate with that magic-auth code. If WorkOS accepts it, the function returns `true`. If WorkOS says `invalid_grant`, it returns `false`, meaning the code was not accepted. Other WorkOS errors become `VerificationError` with a sign-in failure message.

**Call relations**: The onboarding flow calls this when the user submits the code they received. It relies on WorkOS to grade the code, then reports the result as either accepted, retryable wrong code, or non-retryable verification failure.

*Call graph*: 1 external calls (__init__).


##### `ConsoleVerifier.authorization_url`  (lines 183–184)

```
def authorization_url(self, state: str) -> str
```

**Purpose**: Builds a local development sign-in URL instead of sending the browser to WorkOS or Google. This keeps the browser flow working when real WorkOS credentials are not being used.

**Data flow**: It receives the signed state string. It URL-escapes that state and returns a path to the local console sign-in page with the state in the query string.

**Call relations**: When console mode is selected, the onboarding flow calls this in place of the WorkOS verifier’s URL builder. It keeps the same state-carrying pattern, but routes to `AUTH_CONSOLE_PATH` instead of an outside service.

*Call graph*: 1 external calls (quote).


##### `ConsoleVerifier.exchange`  (lines 186–187)

```
async def exchange(self, code: str) -> str
```

**Purpose**: In local development, treats the callback code as the email address itself. This mimics the result of a real Google/WorkOS exchange without contacting any outside service.

**Data flow**: It receives the callback code text, strips surrounding whitespace, lowercases it, and returns it as the email address.

**Call relations**: The console sign-in page submits the typed email in the field normally occupied by an OAuth code. This method is the console-mode counterpart to `WorkosVerifier.exchange`.


##### `ConsoleVerifier.begin`  (lines 189–190)

```
async def begin(self, email: str) -> None
```

**Purpose**: In local development, pretends to send an email verification code by writing the fixed code to the log. This lets developers complete the code path without email delivery.

**Data flow**: It receives an email address. Instead of contacting WorkOS, it logs that email along with the fixed development code `000000`, and returns nothing.

**Call relations**: The onboarding flow calls this in console mode when it would normally ask WorkOS to email a code. It preserves the same shape as the real verifier while replacing delivery with a log message.


##### `ConsoleVerifier.confirm`  (lines 192–193)

```
async def confirm(self, email: str, code: str) -> bool
```

**Purpose**: In local development, checks whether the entered code matches the fixed console code. This gives a simple yes-or-no result without WorkOS.

**Data flow**: It receives an email address and a code. It ignores the email for verification, trims the code, compares it with `000000`, and returns `true` for a match or `false` otherwise.

**Call relations**: The onboarding flow calls this in console mode where it would otherwise call WorkOS. It mirrors `WorkosVerifier.confirm` by returning a boolean that says whether the code was accepted.


##### `console_signin_page`  (lines 196–212)

```
def console_signin_page(state: str) -> str
```

**Purpose**: Creates the simple HTML page used for local development sign-in. The page asks for a work email and sends it to the same callback path that a real WorkOS return would use.

**Data flow**: It receives the signed state string. It escapes the state for safe use inside HTML, inserts it into a hidden form field, and returns a complete HTML document as text. When submitted, the form sends the typed email as the `code` query parameter.

**Call relations**: Console mode routes the browser to this page through `ConsoleVerifier.authorization_url`. The page then feeds the normal callback flow, allowing local testing to exercise the same gateway path as a real sign-in.

*Call graph*: 1 external calls (escape).


##### `workos_console_mode`  (lines 215–216)

```
def workos_console_mode() -> bool
```

**Purpose**: Answers whether the gateway is currently configured to use local console sign-in. This gives other code a simple yes-or-no check.

**Data flow**: It reads the configured WorkOS mode through `_workos_mode`, compares it with the console-mode value, and returns a boolean.

**Call relations**: It is a small wrapper around `_workos_mode`. Other parts of the gateway can call it when they need to decide whether to expose or route console-only sign-in behavior.

*Call graph*: calls 1 internal fn (_workos_mode).


##### `workos_verifier_from_env`  (lines 219–233)

```
def workos_verifier_from_env() -> WorkosVerifier | ConsoleVerifier
```

**Purpose**: Creates the verifier object the gateway should use, based on environment variables. It chooses either the real WorkOS verifier or the local console verifier.

**Data flow**: It reads the mode through `_workos_mode`. If the mode is console, it logs a warning and returns a `ConsoleVerifier`. Otherwise, it requires the WorkOS API key, client id, and redirect URI from the environment, builds an async WorkOS client, and returns a `WorkosVerifier` containing that client and redirect URI.

**Call relations**: This is the setup point for the sign-in verifier. It calls `_require_env` for mandatory production settings, constructs the WorkOS SDK client when needed, and hands back an object with the common verifier methods used during onboarding.

*Call graph*: calls 2 internal fn (_require_env, _workos_mode); 3 external calls (__init__, __init__, AsyncWorkOSClient).


##### `_workos_mode`  (lines 236–240)

```
def _workos_mode() -> str
```

**Purpose**: Reads and validates the configured WorkOS mode. It prevents misspelled or unsupported modes from silently changing sign-in behavior.

**Data flow**: It reads `WORKOS_MODE` from the environment, defaulting to real WorkOS mode. It trims and lowercases the value, checks that it is either `workos` or `console`, and returns it. If the value is something else, it raises `RuntimeError`.

**Call relations**: `workos_console_mode` and `workos_verifier_from_env` both call this before making mode-dependent decisions. It centralizes the rules for what modes are allowed.

*Call graph*: called by 2 (workos_console_mode, workos_verifier_from_env).


##### `_require_env`  (lines 243–247)

```
def _require_env(name: str) -> str
```

**Purpose**: Reads one required environment variable and fails clearly if it is missing. This avoids starting real WorkOS sign-in with incomplete credentials.

**Data flow**: It receives the name of an environment variable. It looks up the value, returns it if present, and raises `RuntimeError` with a clear message if the value is empty or unset.

**Call relations**: `workos_verifier_from_env` calls this for the WorkOS API key, client id, and redirect URI when real WorkOS mode is selected. It keeps startup configuration errors explicit and early.

*Call graph*: called by 1 (workos_verifier_from_env).


### Workspace invitations
Invitation codes, invite emails, workspace membership decisions, and issued gateway tokens complete the onboarding path.

### `control/src/ufo_control/gateway_invite.py`

`domain_logic` · `workspace signup and invite redemption`

This file solves a practical gatekeeping problem: a company should be able to create a workspace only after its email domain has been approved, and one approval should not accidentally create two workspaces. Think of it like a front-desk guest list: the company domain is on the list, not a secret code anyone can pass around.

The database table defined here stores invite grants. Each grant records the approved email address and domain, when it expires, whether it has been used, and optional signup notes about the company. Some invites are tied to a waitlist object number; others come from an intake form and have no number.

The main class, InviteCodes, is the working interface. It can check whether a domain has a live invite, fetch the newest signup profile for that domain, create a new invite, and redeem an invite when a verified user is creating a workspace. The careful part is redemption: it locks the invite row in the database before marking it consumed. A row lock is a database safety catch that stops two simultaneous requests from claiming the same invite at once. It also writes the invite ID onto the workspace claim in the same database transaction, so a crash cannot leave an invite marked used but unattached.

The file also defines small result objects, such as InviteAccepted, InviteExpired, and InviteConsumed, so callers can clearly tell what happened.

#### Function details

##### `InviteCodes.available`  (lines 114–123)

```
async def available(self, email_domain: str) -> bool
```

**Purpose**: Checks whether a given email domain currently has an unused, unexpired invite. A caller can use this to decide whether a verified company email is allowed to proceed with workspace creation.

**Data flow**: It receives an email domain string. It asks the database whether there is a matching invite row whose consumed time is still empty and whose expiry time is still in the future. It returns true if such a row exists, otherwise false; it does not change the database.

**Call relations**: This is a quick lookup into the invite ledger. It does not call the other functions in this file; it simply answers the yes-or-no question that other signup code can use before allowing a workspace flow to continue.


##### `InviteCodes.profile`  (lines 125–137)

```
async def profile(self, email_domain: str) -> SignupProfile | None
```

**Purpose**: Fetches the latest company profile notes collected for an invited domain. These notes tell the new workspace’s agent what the customer said their business does and what they want help with.

**Data flow**: It receives an email domain. It reads the newest invite row for that domain that has profile text, using creation time and ID to break ties. If it finds one, it turns the database fields into a SignupProfile object; if not, it returns None. It only reads data and does not write anything.

**Call relations**: When later workspace setup wants context about the customer, this function supplies it from the invite history. Its only handoff inside this file is creating a SignupProfile object from the database row.

*Call graph*: 1 external calls (__init__).


##### `InviteCodes.mint`  (lines 139–183)

```
async def mint(self, object_number: int | None, email: str, profile: SignupProfile | None=None) -> MintedInvite
```

**Purpose**: Creates a new invite grant for an email domain, after checking that the email is acceptable and that the object number or domain is not already taken by a live or already-used invite. This is the function used when an operator or intake flow approves someone to create a workspace.

**Data flow**: It receives an optional waitlist object number, an email address, and optional profile notes. First it normalizes the email, which means turning it into a consistent form and extracting the domain. It checks that the address follows the work-email rules, checks that profile fields are present and not too long when a profile is supplied, calculates an expiry time, and then opens a database transaction. Inside that transaction it refuses conflicting existing grants, removes expired live grants for the same object or domain, and inserts the new invite row. If another process creates a conflicting invite at the same time, the database uniqueness rule catches it and the function raises InviteError. On success, it returns a MintedInvite showing the object number, normalized email, and expiry time.

**Call relations**: This is the main invite-creation path. It calls normalize_email to standardize the address, WorkEmailPolicy to reject unsuitable email addresses, _refuse_standing to check existing records, uuid4 to create the invite ID, and finally returns a MintedInvite for the caller to display or email. If validation or conflict checks fail, it raises InviteError instead of creating a grant.

*Call graph*: calls 1 internal fn (_refuse_standing); 6 external calls (__init__, __init__, __init__, now, normalize_email, uuid4).


##### `InviteCodes._refuse_standing`  (lines 185–199)

```
async def _refuse_standing(self, connection: asyncpg.Connection, column: str, value: object, subject: str, now: datetime) -> None
```

**Purpose**: Protects the invite ledger from ambiguous or duplicate grants. It checks whether a particular object number or email domain is already known to the system in a way that should block a new invite.

**Data flow**: It receives an open database connection, the database column to check, the value to look for, a human-readable subject name, and the current time. It looks for a matching row that is either already consumed or still unexpired. If none exists, it returns quietly. If the matching row was already consumed, it raises InviteError saying the subject is already identified. If the row is still live, it raises InviteError saying the subject already has an invite and includes the expiry time.

**Call relations**: This is a helper used by InviteCodes.mint while mint is inside its database transaction. Mint asks it first about the waitlist object number when one exists, and then about the email domain, so the new invite is only inserted after both checks pass.

*Call graph*: called by 1 (mint); 2 external calls (__init__, fetchrow).


##### `InviteCodes.redeem`  (lines 201–244)

```
async def redeem(self, email_domain: str, claim_id: UUID) -> InviteUnknown | InviteExpired | InviteConsumed | InviteAccepted
```

**Purpose**: Consumes an invite for a domain when a verified user is creating a workspace. It returns a clear outcome: no invite, expired invite, already-used invite, or accepted invite.

**Data flow**: It receives an email domain and the ID of the workspace claim being created. It opens a database transaction and selects the best matching invite row while locking it, so another simultaneous redemption cannot alter the same row first. If there is no row, it returns InviteUnknown. If the invite was already consumed, it checks whether this same claim is already attached to that invite; if yes, it returns InviteAccepted again, making repeated redemption by the same claim safe. If the invite was consumed by someone else, it returns InviteConsumed. If the invite is past its expiry time, it returns InviteExpired. Otherwise it marks the invite consumed, writes the invite ID onto the workspace claim, and returns InviteAccepted with the invite ID, object number, and consumption time.

**Call relations**: This is the critical one-time-use path. Signup or workspace-creation code calls it after the user has proved ownership of the approved email domain. It creates one of the small result objects, such as InviteUnknown or InviteAccepted, so the caller can decide whether to continue, stop, or report the specific problem.

*Call graph*: 5 external calls (__init__, __init__, __init__, __init__, now).


### `control/src/ufo_control/gateway_email.py`

`io_transport` · `invite creation and outbound email sending`

This file protects the invite flow from two common problems: people using personal throwaway addresses, and the service silently failing to send mail because cloud email settings are missing or wrong. First, it normalizes and checks email addresses. A work email is accepted only if it has a valid shape and its domain is not on the built-in list of free or disposable email providers. This matters because a workspace is meant to map to a real organization, not to a Gmail or temporary inbox.

It also builds the one email message this service sends: an invite with a curl command, the invited address, the expiry time, and the domain whose members can sign in. The sign-in code itself is handled elsewhere, so this message is more like a doorway sign than a secret key.

For delivery, the file offers two senders. `SesEmailSender` talks directly to Amazon SES, Amazon’s email-sending service, using asynchronous HTTP so it does not block the running server. Before sending, it exchanges the pod’s web identity token for short-lived AWS credentials, then signs the SES request with AWS Signature Version 4, which is Amazon’s proof that the request is allowed. `ConsoleEmailSender` is the local-development alternative: it logs the email instead of sending it. `email_sender_from_env` chooses between them using environment variables and fails loudly if required settings are missing.

#### Function details

##### `normalize_email`  (lines 118–126)

```
def normalize_email(email: str) -> tuple[str, str]
```

**Purpose**: Turns an email address into a clean, lowercase form and extracts its domain. It also rejects addresses that do not look like valid email addresses, so later checks cannot be bypassed with odd formatting.

**Data flow**: It receives a raw email string. It trims spaces, lowercases it, checks it against a strict email pattern, and removes any harmless trailing root dot from the domain match. It returns the cleaned full address and the domain, or raises a work-email error if the address is malformed.

**Call relations**: This is the shared front door for email parsing. `WorkEmailPolicy.validate` uses it before deciding whether a domain is allowed, and `invite_email` uses it so the invite text names the correct domain.

*Call graph*: called by 2 (validate, invite_email); 1 external calls (__init__).


##### `WorkEmailPolicy.validate`  (lines 133–137)

```
def validate(self, email: str) -> str
```

**Purpose**: Checks whether an email address belongs to an acceptable work domain. It blocks known personal and disposable email domains so invites are tied to real organizations.

**Data flow**: It receives an email address. It asks `normalize_email` to clean and parse it, then compares the domain against the policy’s denylist. It returns the accepted domain, or raises a work-email error if the domain is blocked.

**Call relations**: This is the policy gate used before a workspace invite can proceed. It relies on `normalize_email` for safe parsing, then adds the business rule about which domains are not allowed.

*Call graph*: calls 1 internal fn (normalize_email); 1 external calls (__init__).


##### `public_apex_host`  (lines 140–148)

```
def public_apex_host() -> str
```

**Purpose**: Finds the public website host that should appear in the invite command. It lets deployments override the default public URL through an environment variable.

**Data flow**: It reads `UFO_PUBLIC_BASE_URL` from the environment, or falls back to the default public URL. It strips the scheme and path, keeping only the host. It returns that host, or raises an error if the configured value is not a proper base URL.

**Call relations**: Invite-building code can call this when it needs the host for the curl command. It uses URL parsing to avoid copying extra pieces like `https://` or a path into the command text.

*Call graph*: 1 external calls (urlsplit).


##### `invite_email`  (lines 151–161)

```
def invite_email(email: str, expires_at: datetime, apex_host: str) -> tuple[str, str]
```

**Purpose**: Builds the subject and plain-text body for an invitation email. It includes the install command, the invited email address, the expiry time, and the domain that will be allowed to sign in.

**Data flow**: It receives an email address, an expiry time, and the public host. It normalizes the email to get the domain, converts the expiry time to UTC, fills those values into the invite template, and returns the subject and body text.

**Call relations**: This prepares the message that an `EmailSender` will deliver. It calls `normalize_email` so the message’s domain matches the same parsing rules used by the work-email policy.

*Call graph*: calls 1 internal fn (normalize_email); 1 external calls (astimezone).


##### `EmailSender.send`  (lines 165–165)

```
async def send(self, email: str, subject: str, text: str) -> None
```

**Purpose**: Defines the common promise that all email senders must keep: given a recipient, subject, and text body, send the message asynchronously. It is a protocol, meaning it describes the expected shape rather than doing the work itself.

**Data flow**: The inputs are the destination email address, subject, and message text. Implementations decide what happens next, such as sending through SES or logging to the console. The expected output is no returned value; success means the send operation completed without raising an error.

**Call relations**: Both real and local senders fit this shape. Code that needs to send an invite can depend on `EmailSender` without caring whether the message goes to Amazon SES or to the process log.


##### `SesEmailSender.send`  (lines 188–209)

```
async def send(self, email: str, subject: str, text: str) -> None
```

**Purpose**: Sends an email through Amazon SES using an asynchronous HTTP request. It is the production path for real outbound mail.

**Data flow**: It receives the recipient, subject, and text body. It first gets temporary AWS credentials, builds the JSON request SES expects, signs that request so AWS can trust it, then posts it to the SES endpoint. It returns nothing on success, and raises an error if SES reports a failed response.

**Call relations**: This is the main production sender behind the `EmailSender` protocol. It calls `_assume_role` to get credentials, `_sigv4_headers` to create signed AWS headers, and then hands the signed request to `httpx.AsyncClient` for network delivery.

*Call graph*: calls 2 internal fn (_assume_role, _sigv4_headers); 3 external calls (now, AsyncClient, dumps).


##### `SesEmailSender._assume_role`  (lines 211–230)

```
async def _assume_role(self) -> SesCredentials
```

**Purpose**: Gets short-lived AWS credentials for sending email. It uses the pod’s web identity token instead of storing long-lived cloud keys in the application.

**Data flow**: It reads the web identity token from the configured file. It sends that token, the AWS role ARN, and session details to AWS STS, the service that grants temporary credentials. It returns a `SesCredentials` object, or raises an error if STS rejects the request.

**Call relations**: `SesEmailSender.send` calls this immediately before sending mail. After the STS network call returns XML, this method hands the response text to `_parse_assume_role_credentials` to extract the credential fields.

*Call graph*: calls 1 internal fn (_parse_assume_role_credentials); called by 1 (send); 1 external calls (AsyncClient).


##### `_parse_assume_role_credentials`  (lines 233–246)

```
def _parse_assume_role_credentials(payload: str) -> SesCredentials
```

**Purpose**: Reads AWS STS’s XML response and pulls out the temporary credential values needed for SES. It turns a raw service response into a simple credential object the sender can use.

**Data flow**: It receives XML text from the STS response. It parses the XML, looks for the access key, secret key, and session token, and returns them as `SesCredentials`. If any required value is missing, it raises an error rather than sending a broken request later.

**Call relations**: `SesEmailSender._assume_role` calls this after AWS STS replies. Inside it, the helper `credential` performs the repeated lookup for each required field.

*Call graph*: called by 1 (_assume_role); 2 external calls (__init__, fromstring).


##### `_parse_assume_role_credentials.credential`  (lines 236–240)

```
def credential(name: str) -> str
```

**Purpose**: Fetches one named credential field from the parsed STS XML. It keeps the repeated XML lookup and missing-value check in one small place.

**Data flow**: It receives the name of a credential field, such as `AccessKeyId`. It searches the parsed XML response for that field under the STS credentials section. It returns the text value, or raises an error if the field is absent or empty.

**Call relations**: This helper is used only inside `_parse_assume_role_credentials`. That outer function calls it once for each credential value needed to build `SesCredentials`.


##### `_sigv4_headers`  (lines 249–282)

```
def _sigv4_headers(host: str, body: bytes, region: str, credentials: SesCredentials, now: datetime) -> dict[str, str]
```

**Purpose**: Creates the HTTP headers required for AWS Signature Version 4, Amazon’s request-signing system. These headers prove that the SES request was made by someone holding valid temporary credentials.

**Data flow**: It receives the target host, request body bytes, AWS region, temporary credentials, and the current time. It hashes the body, builds the canonical request string AWS expects, derives a signing key, computes the signature, and returns a headers dictionary containing dates, security token, content hash, and authorization signature.

**Call relations**: `SesEmailSender.send` calls this just before making the SES HTTP request. It delegates the key-derivation part to `_signing_key`, then hands the completed headers back so the network request can be accepted by AWS.

*Call graph*: calls 1 internal fn (_signing_key); called by 1 (send); 3 external calls (strftime, sha256, new).


##### `_signing_key`  (lines 285–289)

```
def _signing_key(secret_key: str, date_stamp: str, region: str) -> bytes
```

**Purpose**: Derives the special one-use signing key used for AWS Signature Version 4. This is like turning a master key into a dated, region-specific key for exactly this kind of AWS service request.

**Data flow**: It receives the AWS secret key, date stamp, and region. It repeatedly applies HMAC-SHA256, a cryptographic signing operation, with the date, region, SES service name, and AWS request marker. It returns the final bytes used to sign the request string.

**Call relations**: _sigv4_headers calls this while building the SES authorization header. The result is not sent directly; it is used to create the final signature that goes into the HTTP headers.

*Call graph*: called by 1 (_sigv4_headers); 1 external calls (new).


##### `ConsoleEmailSender.send`  (lines 299–300)

```
async def send(self, email: str, subject: str, text: str) -> None
```

**Purpose**: Pretends to send an email by writing it to the application log. This is useful for local development because it avoids needing an Amazon SES account or real email delivery.

**Data flow**: It receives the recipient, subject, and text body. It formats those pieces into a log message. It returns nothing and does not contact any outside service.

**Call relations**: This is the local implementation of the `EmailSender` protocol. `email_sender_from_env` returns it when `UFO_CONTROL_EMAIL_MODE` is set to console, so the rest of the invite flow can behave as if email was sent.


##### `email_sender_from_env`  (lines 303–317)

```
def email_sender_from_env() -> EmailSender
```

**Purpose**: Chooses which email sender the service should use based on environment variables. It makes the production and local-development modes explicit and fails fast on invalid configuration.

**Data flow**: It reads the email mode from the environment, defaulting to SES. For console mode, it creates a `ConsoleEmailSender`. For SES mode, it reads the sender address, region, AWS role ARN, and token-file path, then creates a `SesEmailSender`. If the mode is unknown or required settings are missing, it raises an error.

**Call relations**: Startup or setup code can call this to get an `EmailSender` without knowing the details. It uses `_require_env` for mandatory SES settings and constructs either the console sender or SES sender for later invite delivery.

*Call graph*: calls 1 internal fn (_require_env); 3 external calls (__init__, __init__, Path).


##### `_require_env`  (lines 320–324)

```
def _require_env(name: str) -> str
```

**Purpose**: Reads a required environment variable and stops immediately if it is missing. This prevents the email sender from starting with half-configured cloud settings.

**Data flow**: It receives the name of an environment variable. It looks up the value in the process environment. It returns the value if present, or raises an error explaining which required setting is unset.

**Call relations**: `email_sender_from_env` calls this when building the SES sender. It acts as a clear guardrail before any attempt is made to contact AWS.

*Call graph*: called by 1 (email_sender_from_env).


### `control/src/ufo_control/gateway_shared.py`

`domain_logic` · `hosted onboarding and signup request handling`

This file is part of hosted onboarding: the path where someone signs in with a verified email address and needs to land in the correct shared workspace. The key idea is that a company or group can be represented by its email domain, such as example.com. The file uses that domain to find or create a stable workspace ID, so the same domain points to the same place over time.

It does three main jobs. First, it lists the workspaces an email address may enter: workspaces where that exact email is already a member, plus the workspace connected to the email domain. Second, it creates a new workspace for a domain when needed and seats the first user as an administrator. Third, it joins a user to an existing allowed workspace.

It also protects the main agent prompt from unsafe public form text. Intake form answers can help describe the customer’s business and goals, but the form is unauthenticated. So this file wraps that text as untrusted information and removes prompt-template braces that could otherwise break future agent prompt rendering. Without this file, onboarding would not have a single, careful place to connect verified emails, domain-based workspaces, membership creation, and safe first-agent setup.

#### Function details

##### `_inert`  (lines 35–48)

```
def _inert(answer: str) -> str
```

**Purpose**: This function makes public form text safe to place inside an agent prompt template. It specifically defuses doubled curly braces, which this system uses for prompt variables, so a user cannot accidentally or deliberately leave text that breaks prompt rendering later.

**Data flow**: It receives one text answer from a form. It repeatedly replaces every doubled opening or closing brace with a single brace until no doubled braces remain. It returns the cleaned text, keeping it readable while removing the special pattern that would be treated as a live prompt variable.

**Call relations**: It is used inside agent_prompt when signup intake answers are included in the new workspace’s main agent prompt. Its job is to clean the raw form answers before they are wrapped as untrusted text.

*Call graph*: called by 1 (agent_prompt).


##### `agent_prompt`  (lines 51–70)

```
def agent_prompt(profile: SignupProfile | None) -> str
```

**Purpose**: This function builds the starting prompt for the main agent in a newly created workspace. If there was no intake form, it uses the normal default prompt; if there was a form, it adds the form answers as cautious background information, not as trusted instructions.

**Data flow**: It receives either no signup profile or a profile containing business and goals text. With no profile, it returns the default agent prompt unchanged. With a profile, it cleans the answers with _inert, formats them into a short intake summary, passes that summary through wall, which marks outside text as untrusted, and returns the combined prompt.

**Call relations**: SharedWorkspaces._ensure calls this when it creates the default main agent for a workspace. agent_prompt relies on _inert to neutralize prompt-template syntax and on wall to clearly separate public form data from trusted system instructions.

*Call graph*: calls 1 internal fn (_inert); called by 1 (_ensure); 1 external calls (wall).


##### `serve_dsn`  (lines 73–80)

```
def serve_dsn() -> str
```

**Purpose**: This function reads the database connection string needed for hosted onboarding to write workspace records using the correct database role. If the setting is missing, it fails loudly instead of letting onboarding run in an unsafe or incorrect mode.

**Data flow**: It reads the UFO_CONTROL_SERVE_DSN environment variable. If a value is present, it returns that value. If it is empty or unset, it raises an error explaining that hosted onboarding needs this connection string for the serve role, which is the database identity expected for these writes.

**Call relations**: This helper is available to code that sets up hosted onboarding database access. It is not part of the workspace choice or creation flow in this file, but it supports the same onboarding area by enforcing required configuration.


##### `SharedWorkspaces.choices`  (lines 106–157)

```
async def choices(self, domain: str, email: str) -> tuple[WorkspaceChoice, ...]
```

**Purpose**: This method finds every workspace a verified email address may reasonably enter. It combines exact membership with domain-based access, then labels each option so the user can choose between them.

**Data flow**: It receives a verified email domain and the full email address. It normalizes them, creates a stable domain-based workspace ID using uuid5, and queries the database for two kinds of matches: workspaces where the email is already a member, and the workspace tied to the domain or to an existing first member with that domain. It checks that the domain does not map to multiple workspaces, builds readable labels, marks whether the user is already a member, and returns a tuple of WorkspaceChoice objects.

**Call relations**: This is the discovery step before joining or creating. Later onboarding code can present these choices to the user; the selected WorkspaceChoice can then be passed to SharedWorkspaces.join. It uses uuid5 so the same domain consistently points to the same possible workspace.

*Call graph*: 2 external calls (__init__, uuid5).


##### `SharedWorkspaces.create`  (lines 159–165)

```
async def create(self, domain: str, email: str, profile: SignupProfile | None=None) -> EnsuredWorkspace
```

**Purpose**: This method creates, or ensures the existence of, the shared workspace for a verified email domain. It is used when onboarding should make the domain’s workspace and seat the signing-in user there.

**Data flow**: It receives a domain, an email address, and optional intake profile details. It turns the lowercased domain into a stable workspace ID with uuid5, then passes that ID, the domain, the email, and the profile to _ensure. It returns an EnsuredWorkspace describing the workspace and whether the user is an administrator.

**Call relations**: This is a public entry point on SharedWorkspaces for the create path. It delegates the actual database work and member setup to SharedWorkspaces._ensure, keeping the domain-to-workspace-ID decision in one small wrapper.

*Call graph*: calls 1 internal fn (_ensure); 1 external calls (uuid5).


##### `SharedWorkspaces.join`  (lines 167–184)

```
async def join(self, choice: WorkspaceChoice, domain: str, email: str) -> EnsuredWorkspace
```

**Purpose**: This method seats a verified email address in a workspace the user is allowed to access. If the user is not already a member, it creates the membership; if they are already a member, it simply checks their administrator status.

**Data flow**: It receives the chosen workspace option, the verified domain, and the email address. If the choice says the person is not yet a member, it calls _ensure to create or confirm the workspace and add them. If they are already a member, it enters that workspace context, opens a database transaction, looks up the member’s admin flag, and returns an EnsuredWorkspace. If the membership disappeared between choosing and joining, it raises an error.

**Call relations**: This is the follow-up to SharedWorkspaces.choices when a user selects an existing option. It calls SharedWorkspaces._ensure for domain-authorized non-members, and otherwise uses the workspace context and transaction helpers to safely read the current membership record.

*Call graph*: calls 1 internal fn (_ensure); 4 external calls (__init__, select, workspace_tx, ws).


##### `SharedWorkspaces._ensure`  (lines 186–239)

```
async def _ensure(self, workspace_id: UUID, domain: str, email: str, profile: SignupProfile | None=None) -> EnsuredWorkspace
```

**Purpose**: This method is the core “make sure this workspace and member exist” routine. It creates the workspace if needed, verifies it still belongs to the expected domain, adds the member, creates the default main agent, and reports whether the member is an administrator.

**Data flow**: It receives a workspace ID, domain, email address, and optional signup profile. Inside the target workspace context and a database transaction, it inserts the workspace if missing, locks the workspace row so two signups do not race each other, checks the first member’s email domain if the workspace already exists, creates the member with admin rights only if they are the first member, inserts the default main agent if it is not already present, then reads back the member’s admin flag. It returns an EnsuredWorkspace with the workspace ID as text and the admin result.

**Call relations**: SharedWorkspaces.create and SharedWorkspaces.join both delegate to this method when onboarding must create or confirm real database state. It calls agent_prompt to build the main agent’s prompt, create_member to add the user, email_domain to enforce the domain ownership rule, and the workspace transaction helpers to perform the changes in the correct workspace safely.

*Call graph*: calls 1 internal fn (agent_prompt); called by 2 (create, join); 8 external calls (__init__, insert, select, workspace_tx, create_member, email_domain, ws, uuid4).


### `control/src/ufo_control/gateway_token.py`

`domain_logic` · `request handling`

This file is a thin, purposeful wrapper around the shared token-making code in `ufo.bearer`. A bearer token is a string that proves “whoever holds this is allowed in,” much like a stamped wristband at an event. Here, the token is for a hosted member and is meant to be stored by the client in `~/.ufo/credentials` and later checked by other parts of the system.

The file sets two important facts for gateway-issued member tokens. First, they last for 30 days. Second, the secret used to sign them is expected to come from the `UFO_TOKEN_SECRET` environment variable. Signing with a secret means the token cannot be changed without detection.

Rather than building the token format itself, this file delegates to the one shared codec in `ufo.bearer`. A codec is the code that turns information into a signed token and, elsewhere, back into verified claims. Keeping both minting and verification tied to the same shared token code prevents the token shape from drifting over time. Without this wrapper, callers might accidentally choose different lifetimes or token-building rules, causing valid users to be rejected or insecure tokens to be created.

#### Function details

##### `mint_token`  (lines 13–14)

```
def mint_token(secret: str, workspace_id: str, email: str, now: datetime | None=None) -> str
```

**Purpose**: Creates a gateway bearer token for one member in one workspace. It uses the standard 30-day lifetime for these tokens and relies on shared signing code so the token can later be verified in the expected format.

**Data flow**: It receives a secret string, a workspace ID, an email address, and optionally the current time. It combines those with the fixed 30-day token lifetime and passes them to the shared bearer-token maker. The result is a signed token string; this function does not change files or global state.

**Call relations**: When some higher-level gateway flow needs to issue credentials for a hosted member, it calls this function instead of calling the shared bearer code directly. This function then hands the real token construction to `ufo.bearer.mint_token`, adding only the gateway-specific lifetime rule.

*Call graph*: 1 external calls (mint_token).


### Slack Connect provisioning
Approved customer domains receive retry-safe Slack Connect channels and first invitations.

### `control/src/ufo_control/gateway_slack_connect.py`

`orchestration` · `startup and background polling`

This file is the background worker for signup-time Slack Connect delivery. When a customer domain is approved, the system wants one shared Slack channel in UFO's own Slack workspace, plus one Slack-generated invite email to the approved address. Without this worker, operators would have to create channels and send invites by hand, and signups could arrive before the Slack channel was ready.

The file uses the database as its memory. It first turns approved domains into rows in a `slack_connect_delivery` table. Each row is keyed by email domain, not by invite code, because the project treats one domain as one customer. Then a gateway replica claims one due row, like taking a ticket from a shared queue, and renews that claim while Slack calls are in progress so another replica does not do the same work at the same time.

The delivery steps are careful: prove the Slack bot token belongs to the expected operator workspace, create or find the deterministic channel name, invite the customer, and post a greeting that tells them where to sign in. Temporary failures are retried with backoff. Permanent or ambiguous failures are marked `failed` for an operator to review. A key detail is that the invite attempt is recorded before calling Slack, to avoid sending a duplicate invite after an uncertain response. The greeting is recorded after posting, because a duplicate message is less harmful than a missing sign-in link.

#### Function details

##### `SlackTransientError.__init__`  (lines 200–202)

```
def __init__(self, message: str, retry_after: float | None=None) -> None
```

**Purpose**: This creates an error object for Slack problems that may clear up, such as a timeout, rate limit, or server error. It can also carry Slack's suggested wait time before trying again.

**Data flow**: It receives a message and, optionally, a retry-after delay. It stores the message as the normal error text and keeps the delay on the error object so later code can schedule the next attempt.

**Call relations**: The Slack API wrapper creates this error when `_call` sees a temporary transport or Slack-side failure. The inviter later treats this kind of error as retryable instead of marking the delivery permanently broken.

*Call graph*: called by 1 (_call).


##### `rearm_failed_delivery`  (lines 221–236)

```
async def rearm_failed_delivery(pool: asyncpg.Pool, email_domain: str) -> datetime | None
```

**Purpose**: This is the operator recovery hook for putting one failed Slack Connect delivery back into the retry queue. It never contacts Slack itself; it only resets the database row.

**Data flow**: It receives a database pool and an email domain. If that domain has a row in the failed state, it clears the worker, retry time, attempt count, and last error, changes the state back to pending, and returns the time when the row was last updated before the reset. If there is no failed row to reset, it returns nothing.

**Call relations**: This sits outside the automatic poller as a manual repair tool. After an operator fixes the underlying cause, another command can call it, and the normal background inviter will later pick up the re-armed row.

*Call graph*: 1 external calls (fetchval).


##### `SlackConnectClient.team_id`  (lines 248–249)

```
async def team_id(self) -> str
```

**Purpose**: This asks Slack which workspace the configured bot token belongs to. The inviter uses it as a safety check before creating channels or sending invitations.

**Data flow**: It sends an `auth.test` request through the shared Slack call helper. From Slack's response, it extracts the `team_id` field and returns it as text.

**Call relations**: `SlackConnectInviter._verify_team` calls this at the start of each delivery. It relies on `_call` for the HTTP request and `_text` for safe response reading.

*Call graph*: calls 2 internal fn (_call, _text).


##### `SlackConnectClient.create_channel`  (lines 251–253)

```
async def create_channel(self, name: str) -> str
```

**Purpose**: This creates a public Slack channel with the deterministic customer channel name. It returns Slack's internal channel ID, which is needed for later invite and message calls.

**Data flow**: It receives a channel name, sends it to Slack's `conversations.create` endpoint, then pulls `channel.id` out of the response. If Slack refuses the name or the response is invalid, the helper methods raise the appropriate error.

**Call relations**: `SlackConnectInviter._open_channel` uses this when a delivery row does not already have a channel ID. The function delegates the actual web request to `_call` and response extraction to `_text`.

*Call graph*: calls 2 internal fn (_call, _text).


##### `SlackConnectClient.channel_id_by_name`  (lines 255–276)

```
async def channel_id_by_name(self, name: str) -> str
```

**Purpose**: This finds an existing Slack channel by its exact name, including archived channels. It is used when Slack says the desired channel name is already taken, so the worker can recover instead of giving up immediately.

**Data flow**: It receives the expected channel name. It pages through Slack's public channel list, checks each returned channel's name, and returns the matching channel's ID. If no match appears within the bounded search, it raises a permanent error because Slack's state is inconsistent for this workflow.

**Call relations**: `SlackConnectInviter._open_channel` calls this after `create_channel` raises the special name-taken error. It uses `_call` to fetch each Slack page and `_text` to read the matching ID safely.

*Call graph*: calls 2 internal fn (_call, _text); 1 external calls (__init__).


##### `SlackConnectClient.outgoing_invite_id`  (lines 278–309)

```
async def outgoing_invite_id(self, channel_id: str) -> str | None
```

**Purpose**: This checks whether the channel already has a live outgoing Slack Connect invitation. It helps avoid sending a second invitation after an earlier attempt may have reached Slack but the response was lost.

**Data flow**: It receives a Slack channel ID. It pages through Slack's outgoing Connect invites, looks for entries for that channel, and returns the invite ID only if Slack says the invite is still live, such as sent or accepted. Dead invite statuses are ignored, unknown statuses cause a permanent review error, and no live invite produces `None`.

**Call relations**: `SlackConnectInviter._invite` calls this during reconciliation when a previous invite attempt was already recorded. It uses `_call` for Slack requests and `_text` to extract the invite ID.

*Call graph*: calls 2 internal fn (_call, _text); 1 external calls (__init__).


##### `SlackConnectClient.is_externally_shared`  (lines 311–314)

```
async def is_externally_shared(self, channel_id: str) -> bool
```

**Purpose**: This asks Slack whether a channel is already shared or pending sharing with an outside workspace. It is another way to prove the invite succeeded when Slack no longer lists a usable invite ID.

**Data flow**: It receives a channel ID, requests Slack channel information, and checks the returned channel flags for externally shared or pending externally shared status. It returns `true` if either flag is present, otherwise `false`.

**Call relations**: `SlackConnectInviter._invite` uses this after it cannot find a live outgoing invite for a previously attempted row. The Slack request itself goes through `_call`.

*Call graph*: calls 1 internal fn (_call).


##### `SlackConnectClient.post_message`  (lines 316–319)

```
async def post_message(self, channel_id: str, text: str) -> None
```

**Purpose**: This posts the greeting message into the Slack channel. The greeting tells the customer where to sign in from a browser or install from a terminal.

**Data flow**: It receives a channel ID and message text. It first checks that the message is not too long for the project's limit, then sends it to Slack's `chat.postMessage` endpoint. It returns nothing if Slack accepts the message.

**Call relations**: `SlackConnectInviter._greet` calls this near the end of a successful delivery. The Slack request goes through `_call`; an overlong message becomes a permanent error.

*Call graph*: calls 1 internal fn (_call); 1 external calls (__init__).


##### `SlackConnectClient.invite_shared`  (lines 321–328)

```
async def invite_shared(self, channel_id: str, email: str) -> str
```

**Purpose**: This sends the Slack Connect invitation email for a channel to the approved customer address. It returns Slack's invite ID so the database can remember what was sent.

**Data flow**: It receives a channel ID and an email address. It rejects emails longer than the allowed maximum, sends the invite request to Slack, extracts the returned invite ID, and returns it.

**Call relations**: `SlackConnectInviter._invite` calls this only after recording that an invite attempt is about to happen. It uses `_call` for the Slack API request and `_text` to read the invite ID.

*Call graph*: calls 2 internal fn (_call, _text); 1 external calls (__init__).


##### `SlackConnectClient.redact`  (lines 330–331)

```
def redact(self, message: str) -> str
```

**Purpose**: This removes the bot token from error messages before they are stored or logged. It protects secrets from appearing in logs, database rows, or tracebacks.

**Data flow**: It receives a message string, replaces any occurrence of the bot token with a placeholder, trims the result to a fixed length, and returns the safe text.

**Call relations**: The inviter uses this when recording retryable or failed errors. It is part of the safety boundary around Slack failures, so diagnostics remain useful without leaking credentials.


##### `SlackConnectClient._call`  (lines 333–361)

```
async def _call(self, method: str, params: dict[str, str | int]) -> dict[str, Any]
```

**Purpose**: This is the common Slack Web API request helper. It turns raw HTTP and Slack responses into normal payloads or into clear retryable versus permanent errors.

**Data flow**: It receives a Slack method name and parameters. It sends an authenticated HTTP POST to Slack, omitting empty parameter values, then interprets the HTTP status and Slack JSON response. Successful calls return the parsed response dictionary; rate limits, timeouts, server errors, known temporary Slack errors, name conflicts, and permanent Slack errors become specific exceptions.

**Call relations**: All higher-level Slack client methods call this instead of doing HTTP themselves. It creates `SlackTransientError` for retryable situations, uses `_retry_after` for rate-limit delays, and raises terminal errors when retrying would not safely fix the problem.

*Call graph*: calls 2 internal fn (__init__, _retry_after); called by 7 (channel_id_by_name, create_channel, invite_shared, is_externally_shared, outgoing_invite_id, post_message, team_id); 3 external calls (__init__, __init__, AsyncClient).


##### `SlackConnectClient._text`  (lines 363–369)

```
def _text(self, payload: dict[str, Any], *path: str) -> str
```

**Purpose**: This safely extracts a text value from a nested Slack response. It prevents later code from silently accepting a malformed response.

**Data flow**: It receives a response dictionary and a path of keys. It walks through the dictionary one key at a time, raises a permanent error if any key is missing or the structure is wrong, and returns the final value as a string.

**Call relations**: Slack client methods use this after `_call` succeeds when they need IDs such as team ID, channel ID, or invite ID. It keeps response checking in one small, consistent place.

*Call graph*: called by 5 (channel_id_by_name, create_channel, invite_shared, outgoing_invite_id, team_id); 1 external calls (__init__).


##### `_retry_after`  (lines 372–376)

```
def _retry_after(response: httpx.Response) -> float | None
```

**Purpose**: This reads Slack's rate-limit wait hint from an HTTP response. It caps the value so one bad or very large header does not pause delivery for too long.

**Data flow**: It receives an HTTP response, looks for the `retry-after` header, and checks that it is a number. If it is valid, it returns the number of seconds up to the configured maximum; otherwise it returns `None`.

**Call relations**: `SlackConnectClient._call` uses this when Slack responds with HTTP 429, meaning too many requests. The resulting delay is carried on `SlackTransientError` for the retry scheduler.

*Call graph*: called by 1 (_call).


##### `SlackConnectInviter.run`  (lines 404–428)

```
async def run(self) -> None
```

**Purpose**: This is the never-ending background loop for Slack Connect delivery. It keeps polling for work and deliberately stays alive even when individual sweeps fail.

**Data flow**: It starts with a failure counter at zero, repeatedly calls `poll`, and resets the counter after a successful sweep. If polling raises an unexpected exception, it logs the failure and continues. When no row was claimed, it sleeps for the configured interval before checking again.

**Call relations**: The gateway starts this task when Slack Connect delivery is enabled. It calls `poll` for each sweep and uses sleep only when the queue is not immediately busy.

*Call graph*: calls 1 internal fn (poll); 1 external calls (sleep).


##### `SlackConnectInviter.poll`  (lines 430–445)

```
async def poll(self) -> bool
```

**Purpose**: This performs one unit of background work: discover newly eligible domains, claim one due delivery row, and advance it. It returns whether it actually claimed work.

**Data flow**: It first materializes eligible customer domains into the delivery table. Then it tries to claim one pending or expired row. If none exists, it returns `false`. If it claims one, it starts a lease-renewal task, advances the delivery through Slack, cancels the renewal task, waits for cleanup, and returns `true`.

**Call relations**: `run` calls this on every sweep. It coordinates `_materialize`, `_claim`, `_renew_lease`, and `_advance`, acting like the dispatcher for a single delivery attempt.

*Call graph*: calls 4 internal fn (_advance, _claim, _materialize, _renew_lease); called by 1 (run); 2 external calls (create_task, gather).


##### `SlackConnectInviter._materialize`  (lines 447–461)

```
async def _materialize(self) -> None
```

**Purpose**: This fills the Slack delivery table with rows for domains that have earned Slack Connect delivery. It makes the database queue reflect approved grants and created workspaces.

**Data flow**: It opens a database transaction, takes a PostgreSQL advisory lock, and runs an insert-from-select statement. The statement derives the channel name in the database and skips conflicts, so duplicate domains or duplicate readable names do not abort the whole batch.

**Call relations**: `poll` calls this before claiming work, so every sweep first discovers any newly eligible customers. The advisory lock keeps multiple gateway replicas from racing while they materialize the same rows.

*Call graph*: called by 1 (poll).


##### `SlackConnectInviter._claim`  (lines 463–476)

```
async def _claim(self) -> _Delivery | None
```

**Purpose**: This leases one due delivery row for the current worker. A lease is a temporary claim that says, in effect, 'this replica is working on this customer now.'

**Data flow**: It runs the claim query with the worker ID and lease length. If no row is due, it returns `None`. If a row is claimed, it converts the database fields into a `_Delivery` object containing the customer domain, email, channel state, invite state, greeting state, and attempt count.

**Call relations**: `poll` calls this after materializing rows. If it returns a delivery, `poll` starts lease renewal and hands the delivery to `_advance`.

*Call graph*: called by 1 (poll); 1 external calls (__init__).


##### `SlackConnectInviter._renew_lease`  (lines 478–500)

```
async def _renew_lease(self, email_domain: str) -> None
```

**Purpose**: This keeps a claimed row from expiring while slow Slack calls are still running. It reduces the chance that another gateway replica will pick up the same row and send a duplicate invite.

**Data flow**: It receives the email domain for the claimed row. In a loop, it sleeps for the renewal interval, then updates that row's expiration time if it is still owned by this worker. Database errors are logged and retried; cancellation exits the loop.

**Call relations**: `poll` starts this as a companion task while `_advance` works on the row, then cancels it afterward. The actual delivery writes still verify ownership through `_write`.

*Call graph*: called by 1 (poll); 1 external calls (sleep).


##### `SlackConnectInviter._advance`  (lines 502–531)

```
async def _advance(self, delivery: _Delivery) -> None
```

**Purpose**: This carries one claimed delivery through the full Slack workflow. It is the main step-by-step recipe for turning a pending row into a delivered, retry-scheduled, or failed row.

**Data flow**: It receives a `_Delivery` snapshot. It verifies the Slack team, opens or finds the channel, sends or reconciles the invitation, and posts the greeting if needed. Retryable Slack errors reschedule the row, permanent errors fail it, lost leases are passed upward, and unexpected errors are logged and recorded as failures. If everything succeeds, it marks the row delivered.

**Call relations**: `poll` calls this after claiming a row. It delegates each stage to `_verify_team`, `_open_channel`, `_invite`, and `_greet`, and uses `_write`, `_reschedule`, or `_fail` to update the database outcome.

*Call graph*: calls 7 internal fn (_fail, _greet, _invite, _open_channel, _reschedule, _verify_team, _write); called by 1 (poll).


##### `SlackConnectInviter._verify_team`  (lines 533–541)

```
async def _verify_team(self) -> None
```

**Purpose**: This confirms the Slack bot token belongs to the expected operator workspace before changing anything. It prevents a misconfigured token from creating channels in the wrong Slack workspace.

**Data flow**: It asks the Slack client for the token's team ID and compares it with the configured expected team ID. If they match, it returns normally. If they differ, it raises a configuration error that will fail the row for operator review.

**Call relations**: `_advance` calls this as the first Slack-related step. It relies on `SlackConnectClient.team_id` to ask Slack what workspace the token represents.

*Call graph*: called by 1 (_advance); 1 external calls (__init__).


##### `SlackConnectInviter._open_channel`  (lines 543–549)

```
async def _open_channel(self, delivery: _Delivery) -> str
```

**Purpose**: This ensures the customer has a Slack channel and records its Slack ID in the database. It can recover when the create response was lost but the channel already exists.

**Data flow**: It receives the delivery row. It asks Slack to create the deterministic channel name. If Slack says the name is taken, it looks up the existing channel by that exact name. Then it writes the channel ID onto the delivery row and returns the ID.

**Call relations**: `_advance` calls this when the delivery does not already have a channel ID. It updates the row through `_write`, so the update only succeeds while this worker still owns the lease.

*Call graph*: calls 1 internal fn (_write); called by 1 (_advance).


##### `SlackConnectInviter._invite`  (lines 551–568)

```
async def _invite(self, delivery: _Delivery, channel_id: str) -> str | None
```

**Purpose**: This ensures the Slack Connect invitation has been sent, without blindly sending a duplicate after an uncertain earlier attempt. It is the most careful part of the workflow because duplicate customer invite emails are the main thing this design avoids.

**Data flow**: It receives the delivery row and channel ID. If an invite was previously attempted, it first checks Slack for a live outgoing invite, then checks whether the channel is already externally shared. If neither proves success, or if this is a fresh row, it records `invite_attempted_at`, sends the Slack invite to the stored email, saves the returned invite ID, and returns it. If the channel is already shared but no invite ID is available, it returns `None`.

**Call relations**: `_advance` calls this after the channel is ready. It uses `_persist_invitation` to save an invite ID and `_write` to mark that an invite attempt is about to be made.

*Call graph*: calls 2 internal fn (_persist_invitation, _write); called by 1 (_advance).


##### `SlackConnectInviter._greet`  (lines 570–578)

```
async def _greet(self, delivery: _Delivery, channel_id: str) -> None
```

**Purpose**: This posts the welcome/sign-in message into the customer's Slack channel and records that it was posted. The message matters because it contains the browser login link.

**Data flow**: It receives the delivery row and channel ID. It formats the greeting with the customer's email domain and the public host, posts it to Slack, then writes `greeted_at` to the database after Slack confirms the post.

**Call relations**: `_advance` calls this after the invitation step when the row has not yet been greeted. It uses `_write` for the marker so lease ownership is checked before the database is changed.

*Call graph*: calls 1 internal fn (_write); called by 1 (_advance).


##### `SlackConnectInviter._persist_invitation`  (lines 580–582)

```
async def _persist_invitation(self, delivery: _Delivery, invitation_id: str) -> str
```

**Purpose**: This saves Slack's invitation ID on the delivery row. It gives the system a durable record that Slack accepted or already had a live invitation.

**Data flow**: It receives the delivery row and invitation ID, writes that ID into the row, and returns the same ID to its caller.

**Call relations**: `_invite` calls this after sending a new invite or finding a live existing invite. The actual database update goes through `_write` to confirm this worker still owns the row.

*Call graph*: calls 1 internal fn (_write); called by 1 (_invite).


##### `SlackConnectInviter._reschedule`  (lines 584–603)

```
async def _reschedule(self, delivery: _Delivery, error: SlackTransientError) -> None
```

**Purpose**: This handles a temporary Slack problem by putting the row back into the pending queue for a later attempt. If the row has already tried too many times, it fails the row instead.

**Data flow**: It receives the delivery row and a retryable error. If the attempt count has reached the limit, it passes the row to `_fail`. Otherwise it chooses a delay from Slack's retry hint or an exponential backoff schedule, clears the worker claim, stores the next attempt time and redacted error text, and logs the retry.

**Call relations**: `_advance` calls this when Slack or the network fails in a way that may recover. It uses `_write` to save the schedule, or `_fail` when retries are exhausted.

*Call graph*: calls 2 internal fn (_fail, _write); called by 1 (_advance); 1 external calls (timedelta).


##### `SlackConnectInviter._fail`  (lines 605–616)

```
async def _fail(self, delivery: _Delivery, error: Exception) -> None
```

**Purpose**: This marks a delivery row as failed for operator review. It is used when retrying would not safely fix the problem, or when retry attempts have run out.

**Data flow**: It receives the delivery row and an error. It redacts secrets from the error text, writes the failed state and last error into the database, clears the active claim and next retry time, and logs the failure.

**Call relations**: `_advance` calls this for permanent and unexpected errors. `_reschedule` also calls it when a retryable problem has exceeded the maximum attempt count. Like other state changes, it writes through `_write`.

*Call graph*: calls 1 internal fn (_write); called by 2 (_advance, _reschedule).


##### `SlackConnectInviter._write`  (lines 618–627)

```
async def _write(self, email_domain: str, assignment: str, *values: object) -> None
```

**Purpose**: This is the guarded database write helper for claimed delivery rows. It makes sure only the worker that currently owns the lease can change the row.

**Data flow**: It receives an email domain, a SQL assignment fragment, and any extra values for that assignment. It updates the matching row only if the row is still owned by this worker. If the update succeeds, it returns nothing; if no owned row was updated, it raises a lease-lost error.

**Call relations**: Most `_advance` substeps use this to record progress, failures, retries, channel IDs, invite IDs, and greeting markers. If another replica has taken over the row, `_write` stops the current worker from overwriting its state.

*Call graph*: called by 7 (_advance, _fail, _greet, _invite, _open_channel, _persist_invitation, _reschedule); 1 external calls (__init__).


##### `slack_connect_from_env`  (lines 630–646)

```
def slack_connect_from_env(pool: asyncpg.Pool) -> SlackConnectInviter | None
```

**Purpose**: This builds the Slack Connect background inviter from environment variables, or disables it when the feature switch is off. It prevents a half-configured deployment from quietly reaching Slack.

**Data flow**: It receives a database pool and reads the enable flag from the process environment. If disabled, it returns `None`. If enabled, it requires the bot token and expected Slack team ID, reads the public host, builds a worker ID from hostname and process ID, constructs the Slack client and inviter, and returns the inviter. If the enable flag is not a valid boolean value, it raises an error.

**Call relations**: Gateway startup code can call this to decide whether to launch `SlackConnectInviter.run`. It uses `_require_env` for required settings and constructs the main objects used by the polling loop.

*Call graph*: calls 1 internal fn (_require_env); 5 external calls (__init__, __init__, getpid, gethostname, public_apex_host).


##### `_require_env`  (lines 649–653)

```
def _require_env(name: str) -> str
```

**Purpose**: This reads one required environment variable and fails clearly if it is missing. It is used only when Slack Connect delivery has been explicitly enabled.

**Data flow**: It receives an environment variable name, looks up its value, and returns the value if it is non-empty. If the value is missing or empty, it raises an error naming the missing setting.

**Call relations**: `slack_connect_from_env` calls this for the Slack bot token and expected team ID. This keeps startup validation simple and loud.

*Call graph*: called by 1 (slack_connect_from_env).

## 📊 State Registers Touched

- `reg-workspace-roster` — The saved list of workspaces, members, admins, seats, and membership rules.
- `reg-auth-tokens` — The signed tickets and login tokens used to prove access to sessions, downloads, sandbox links, and hosted onboarding.
- `reg-onboarding-ledger` — The temporary claims, invitations, verified email proofs, and hosted sign-in records used to create or join workspaces.
- `reg-credential-vault` — The encrypted store of workspace secrets and API keys that tools and connectors can request through guarded paths.
- `reg-surface-ingress` — The shared records that connect external surfaces like web, Slack, shell, OAuth, and inbound messages to conversations and replies.
- `reg-billing-export` — The billing integration state for exported usage, member counts, Stripe setup, Metronome sync, and BYOK reporting.
